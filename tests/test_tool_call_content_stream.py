"""从 OpenAI 兼容 HTTP SSE 到应用事件，验证带工具调用的 AIMessage 文本。"""

import asyncio
import json

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core.chat_model import ProviderChatOpenAI
from melonclaw.core.tool_catalog import catalog_fingerprint
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.output.events import iter_research_events
from melonclaw.tool.tool_discovery import build_tool_discovery


@pytest.mark.parametrize("with_selector", [False, True])
def test_content_streams_before_and_during_tool_call_generation_and_survives_execution(with_selector):
    async def run():
        finish_generation = asyncio.Event()
        finish_tool = asyncio.Event()
        requests = []
        preamble = "我先检索。"
        during_call = "正在组织检索参数。"
        final = "检索完毕，开始整理结果。"

        def frame(delta, finish_reason=None):
            return ("data: " + json.dumps({
                "id": "probe", "object": "chat.completion.chunk", "model": "probe-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }, ensure_ascii=False) + "\n\n").encode()

        class Body(httpx.AsyncByteStream):
            def __init__(self, *, selection, has_result):
                self.selection = selection
                self.has_result = has_result

            async def __aiter__(self):
                if self.selection:
                    yield frame({"role": "assistant", "content": '{"tools": ["read_probe"]}'})
                elif self.has_result:
                    yield frame({"role": "assistant", "content": final})
                else:
                    yield frame({"role": "assistant", "content": preamble})
                    yield frame({"tool_calls": [{
                        "index": 0, "id": "call-probe", "type": "function",
                        "function": {"name": "read_probe", "arguments": '{"query":"'},
                    }]})
                    await finish_generation.wait()
                    # 同一个增量同时有 content 与 tool_call 参数，二者不能互斥处理。
                    yield frame({"content": during_call, "tool_calls": [{
                        "index": 0, "function": {"arguments": 'test"}'},
                    }]})
                yield frame({}, "stop" if self.selection or self.has_result else "tool_calls")
                yield b"data: [DONE]\n\n"

        def respond(request):
            body = json.loads(request.content)
            messages = body["messages"]
            if with_selector:
                assert {t["function"]["name"] for t in body["tools"]} == {"find_tools", "read_probe"}
            selection = messages[0]["role"] == "system" and "你是工具路由器" in messages[0]["content"]
            has_result = any(m["role"] == "tool" for m in messages)
            requests.append("selector" if selection else "answer")
            if body.get("stream"):
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      stream=Body(selection=selection, has_result=has_result))
            assert selection
            return httpx.Response(200, json={
                "id": "selector", "object": "chat.completion", "model": "probe-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": '{"tools": ["read_probe"]}',
                }}],
            })

        @tool
        async def read_probe(query: str) -> str:
            """Read an offline test result."""
            assert query == "test"
            await finish_tool.wait()
            return "probe result"

        @tool
        def other_probe() -> str:
            """Unselected tool."""
            raise AssertionError("Unselected tool must not run")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ProviderChatOpenAI(
                model="probe-model", api_key="offline-placeholder", base_url="https://probe.invalid/v1",
                http_async_client=client,
            )
            middleware = [ToolPoolMiddleware(
                model=model, catalog_tools=[read_probe, other_probe], pool_size=1,
            )] if with_selector else []
            agent = create_agent(model, tools=[read_probe, other_probe, build_tool_discovery()], middleware=middleware, checkpointer=InMemorySaver())
            projector = AssistantStepAccumulator(message_id="answer", run_id="probe")
            config = {"configurable": {"thread_id": "probe"}}
            if with_selector:
                await agent.aupdate_state(config, {
                    "messages": [{"role": "user", "content": "test", "id": "u"}],
                    "tool_pool": {"fingerprint": catalog_fingerprint([read_probe, other_probe]),
                                  "turn_id": "u", "names": ["read_probe"], "processed": [], "outcome": "selected"},
                }, as_node="ToolPoolMiddleware.before_model")
            stream = iter_research_events(
                agent, {"messages": [{"role": "user", "content": "test", "id": "u"}]},
                config, projector=projector,
            )
            emitted = []
            try:
                async with asyncio.timeout(5):
                    async for event in stream:
                        emitted.append(event)
                        if event == {"type": "run_phase", "phase": "preparing_tools"}:
                            break
                assert "".join(e["delta"] for e in emitted if e["type"] == "assistant_text_delta") == preamble
                assert not any(e["type"] == "assistant_step_completed" for e in emitted)
                assert not finish_tool.is_set()
                finish_generation.set()
                async with asyncio.timeout(5):
                    async for event in stream:
                        emitted.append(event)
                        if event["type"] == "assistant_tool_call":
                            break
                assert "".join(e["delta"] for e in emitted if e["type"] == "assistant_text_delta") == preamble + during_call
                assert not finish_tool.is_set()
                assert not any(e["type"] == "assistant_tool_result" for e in emitted)
                finish_tool.set()
                async with asyncio.timeout(5):
                    async for event in stream:
                        emitted.append(event)
                snapshot = projector.terminal_snapshot(status="completed", final_content=final)
                assert [s["content"] for s in snapshot] == [preamble + during_call, final]
                assert snapshot[0]["tool_calls"][0]["name"] == "read_probe"
                assert snapshot[0]["tool_calls"][0]["result_preview"] == "probe result"
                assert requests == ["answer", "answer"]
            finally:
                finish_generation.set()
                finish_tool.set()
                await stream.aclose()

    asyncio.run(run())
