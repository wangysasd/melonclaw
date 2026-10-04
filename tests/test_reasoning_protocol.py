"""HTTP 协议、真实图流与模型回传共同验证推理，不调用外部供应商。"""

import asyncio
import json

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from test_model_configs import make_provider_row, make_row

from melonclaw.core.chat_model import ProviderChatOpenAI, build_chat_model
from melonclaw.core.model_catalog import resolve_model_row
from melonclaw.core.reasoning import ThinkTagParser
from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.output.events import iter_research_events


@pytest.mark.parametrize("mode", ["reasoning_content", "reasoning_details", "think_tags"])
def test_reasoning_stream_order_checkpoint_and_tool_request_round_trip(mode):
    async def run():
        requests = []
        release = asyncio.Event()

        def frame(delta, finish=None):
            return ("data: " + json.dumps({"id": "probe", "object": "chat.completion.chunk", "model": "probe",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}, ensure_ascii=False) + "\n\n").encode()

        def reasoning(value):
            if mode == "reasoning_content":
                return {"reasoning_content": value}
            if mode == "reasoning_details":
                return {"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": value,
                    "id": "r0", "format": "anthropic-claude-v1", "signature": "opaque-signature"}]}
            return {"content": f"<think>{value}</think>"}

        class Body(httpx.AsyncByteStream):
            def __init__(self, final):
                self.final = final

            async def __aiter__(self):
                if self.final:
                    yield frame(reasoning("结果分析"))
                    yield frame({"content": "最终回答"})
                else:
                    yield frame(reasoning("先分析"))
                    await release.wait()
                    yield frame({"content": "先读取"})
                    yield frame(reasoning("再分析"))
                    if mode == "reasoning_details":
                        yield frame({"reasoning_details": [{"index": 1, "type": "reasoning.encrypted",
                            "data": "opaque-encrypted-data", "id": "r1", "format": "anthropic-claude-v1"}]})
                    yield frame({"tool_calls": [{"index": 0, "id": "read-1", "type": "function",
                        "function": {"name": "read_probe", "arguments": "{}"}}]})
                yield frame({}, "stop" if self.final else "tool_calls")
                yield b"data: [DONE]\n\n"

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            final = any(m["role"] == "tool" for m in body["messages"])
            if final:
                previous = next(m for m in body["messages"] if m["role"] == "assistant")
                assert previous["tool_calls"][0]["id"] == "read-1"
                if mode == "think_tags":
                    assert previous["content"] == "<think>先分析</think>先读取<think>再分析</think>"
                else:
                    assert previous["content"] == "先读取"
                    expected = "先分析再分析"
                    if mode == "reasoning_content":
                        assert previous[mode] == expected
                    else:
                        assert previous[mode] == [*reasoning("先分析")[mode], *reasoning("再分析")[mode],
                            {"index": 1, "type": "reasoning.encrypted", "data": "opaque-encrypted-data",
                                "id": "r1", "format": "anthropic-claude-v1"}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body(final))

        @tool
        def read_probe() -> str:
            """Read an offline result."""
            return "结果"

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ProviderChatOpenAI(model="probe", api_key="offline-placeholder",
                base_url="https://probe.invalid/v1", http_async_client=client, reasoning_format=mode)
            agent = create_agent(model, tools=[read_probe], checkpointer=InMemorySaver())
            projector = AssistantStepAccumulator(message_id="answer", run_id="run")
            config = {"configurable": {"thread_id": "probe"}}
            stream = iter_research_events(agent, {"messages": [{"role": "user", "content": "test"}]}, config, projector=projector)
            events = []
            try:
                async with asyncio.timeout(5):
                    async for event in stream:
                        events.append(event)
                        if event["type"] == "assistant_text_delta":
                            assert event["delta"] == "先分析"
                            assert event["content_kind"] == "reasoning"
                            break
                assert len(requests) == 1
                release.set()
                async with asyncio.timeout(5):
                    async for event in stream:
                        events.append(event)
                snapshot = projector.terminal_snapshot(status="completed", final_content="最终回答")
                assert snapshot[0]["content_blocks"] == [
                    {"type": "reasoning", "text": "先分析"},
                    {"type": "text", "text": "先读取"},
                    {"type": "reasoning", "text": "再分析"},
                ]
                assert snapshot[1]["content_blocks"] == [
                    {"type": "reasoning", "text": "结果分析"}, {"type": "text", "text": "最终回答"},
                ]
                assert "opaque" not in str(snapshot)
                messages = (await agent.aget_state(config)).values["messages"]
                assert messages[-1].additional_kwargs
                timings = [e for e in events if e["type"] == "run_activity" and e["status"] == "completed"]
                assert len(timings) == 2
                assert all("first_reasoning_ms" in e and "first_text_ms" in e for e in timings)
            finally:
                release.set()
                await stream.aclose()
    asyncio.run(run())


def test_protocol_option_is_local_and_never_sent_as_extra_body():
    provider = make_provider_row(extra_config={"_melonclaw": {"reasoning_format": "reasoning_content"}, "thinking": {"type": "enabled"}})
    model = build_chat_model(resolve_model_row(make_row(), provider))
    assert model.reasoning_format == "reasoning_content"
    assert model.extra_body == {"thinking": {"type": "enabled"}}


def test_think_tags_at_every_split_and_unfinished_reasoning():
    value = "正文<think>分析</think>答复<think>继续思考"
    for split in range(len(value) + 1):
        parser = ThinkTagParser()
        blocks = [*parser.feed(value[:split]), *parser.feed(value[split:]), *parser.feed("", final=True)]
        combined = []
        for block in blocks:
            key = "reasoning" if block["type"] == "reasoning" else "text"
            if combined and combined[-1]["type"] == block["type"]:
                combined[-1][key] += block[key]
            else:
                combined.append(block)
        assert combined == [{"type": "text", "text": "正文"}, {"type": "reasoning", "reasoning": "分析"},
            {"type": "text", "text": "答复"}, {"type": "reasoning", "reasoning": "继续思考"}]


@pytest.mark.parametrize("mode", ["reasoning_content", "reasoning_details", "think_tags"])
def test_complete_provider_messages_preserve_protocol_fields(mode):
    model = ProviderChatOpenAI(model="probe", api_key="offline-placeholder", reasoning_format=mode)
    raw = {"role": "assistant", "content": "正文"}
    if mode == "think_tags":
        raw["content"] = "<think>分析</think>正文"
    elif mode == "reasoning_content":
        raw[mode] = "分析"
    else:
        raw[mode] = [{"type": "reasoning.text", "text": "分析", "signature": "opaque-signature"},
            {"type": "reasoning.encrypted", "data": "opaque-data"}]
    message = model._create_chat_result({"choices": [{"message": raw, "finish_reason": "stop"}]}).generations[0].message
    projector = AssistantStepAccumulator(message_id="a", run_id="r")
    step = projector.start_step()["step"]["id"]
    projector.complete_step(step, content=message.content)
    assert "opaque" not in str(projector.steps)
    payload = model._get_request_payload([message])
    assert payload["messages"][0]["content"] == raw["content"]
    if mode != "think_tags":
        assert payload["messages"][0][mode] == raw[mode]
