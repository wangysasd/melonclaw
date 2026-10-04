"""通过真实 v3 流验证内部选择器隔离、实时阶段和正常工具/正文。"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.output.events import _consume_run_phases, iter_research_events
from melonclaw.tool.tool_discovery import build_tool_discovery


def make_agent(release, *, streaming, fail=False):
    calls = []
    bound_tools = []
    final_text = '<think>回答分析</think>{"tools": []}'

    class WholeSelector(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        async def _agenerate(self, *args, **kwargs):
            await release.wait()
            if fail:
                raise ValueError("selector unavailable")
            return await super()._agenerate(*args, **kwargs)

    class StreamingSelector(WholeSelector):
        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_call_chunks=[{'id': 'selection', 'name': 'ToolSelectionResponse', 'args': '{"tools": [', 'index': 0}]))
            await release.wait()
            if fail:
                raise ValueError("selector unavailable")
            yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_call_chunks=[{'id': None, 'name': None, 'args': '"research_tool"]}', 'index': 0}]))

    class MainModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            bound_tools.append([item.name for item in tools])
            return self

        async def _astream(self, messages, **kwargs):
            if not any(isinstance(m, ToolMessage) for m in messages):
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"id": "find-1", "name": "find_tools", "args": '{"query":"research"}', "index": 0},
                ]))
                return
            if not calls and not fail:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"id": "research-1", "name": "research_tool", "args": '{"query":"test"}', "index": 0},
                ]))
            else:
                yield ChatGenerationChunk(message=AIMessageChunk(content=final_text))

    @tool
    def research_tool(query: str) -> str:
        """Read research results (no external API in this test)."""
        calls.append(query)
        return "research result"

    @tool
    def unused_tool(query: str) -> str:
        """Unselected catalog tool."""
        raise AssertionError("Unselected tools must not execute")

    selector_class = StreamingSelector if streaming else WholeSelector
    selector = selector_class(responses=[AIMessage(content='', tool_calls=[{'id': 'selection', 'name': 'ToolSelectionResponse', 'args': {'tools': ['research_tool']}}])])
    agent = create_agent(
        MainModel(responses=[]), tools=[research_tool, unused_tool, build_tool_discovery()],
        middleware=[ToolPoolMiddleware(
            model=selector, catalog_tools=[research_tool, unused_tool], pool_size=1,
        )],
    )
    return agent, calls, bound_tools, final_text


@pytest.mark.parametrize("streaming", [True, False])
def test_internal_selector_never_enters_real_v3_messages_or_snapshot(streaming):
    async def run():
        release = asyncio.Event()
        agent, calls, bound_tools, final_text = make_agent(release, streaming=streaming)
        projector = AssistantStepAccumulator(message_id="answer", run_id="run")
        stream = iter_research_events(
            agent, {"messages": [{"role": "user", "content": "test"}]}, {}, projector=projector,
        )
        emitted = []
        try:
            async with asyncio.timeout(5):
                async for event in stream:
                    emitted.append(event)
                    if event == {"type": "run_phase", "phase": "selecting_tools"}:
                        break
            # 选择模型仍被阻塞时，阶段已经到达；选择器的部分 JSON 不可见。
            assert not release.is_set()
            assert not any('ToolSelectionResponse' in str(e) for e in emitted)
            release.set()
            async with asyncio.timeout(5):
                async for event in stream:
                    emitted.append(event)
            assert calls == ["test"]
            assert bound_tools and bound_tools[0] == ["find_tools"] and all("research_tool" in names for names in bound_tools[1:])
            assert "".join(e["delta"] for e in emitted if e["type"] == "assistant_text_delta") == final_text
            assert any(e["type"] == "assistant_tool_result" for e in emitted)
            assert any(e == {"type": "run_phase", "phase": "waiting_model"} for e in emitted)
            snapshot = projector.terminal_snapshot(status="completed", final_content=final_text)
            assert len(snapshot) == 3
            assert snapshot[-1]["content"] == final_text
            assert snapshot[-1]["is_final"] is True
            assert '"research_tool"]' not in str(snapshot)
        finally:
            release.set()
            await stream.aclose()

    asyncio.run(run())


def test_selector_failure_degrades_without_leaking_partial_json():
    async def run():
        release = asyncio.Event()
        agent, _, bound_tools, final_text = make_agent(release, streaming=True, fail=True)
        projector = AssistantStepAccumulator(message_id="answer", run_id="run")
        release.set()
        emitted = [event async for event in iter_research_events(
            agent, {"messages": [{"role": "user", "content": "test"}]}, {}, projector=projector,
        )]
        assert all(names == ["find_tools"] for names in bound_tools)
        assert any(e.get("outcome") == "degraded" for e in emitted)
        assert all('research_tool"]' not in str(e) for e in emitted)
        assert projector.steps[-1]["content"] == final_text
    asyncio.run(run())


def test_cancelling_during_selection_keeps_an_empty_assistant_snapshot():
    async def run():
        release = asyncio.Event()
        agent, calls, bound_tools, _ = make_agent(release, streaming=True)
        projector = AssistantStepAccumulator(message_id="answer", run_id="run")
        stream = iter_research_events(
            agent, {"messages": [{"role": "user", "content": "test"}]}, {}, projector=projector,
        )
        try:
            async with asyncio.timeout(5):
                async for event in stream:
                    if event == {"type": "run_phase", "phase": "selecting_tools"}:
                        break
                await stream.aclose()
            assert all('ToolSelectionResponse' not in str(step) for step in projector.steps)
            assert calls == [] and bound_tools == [["find_tools"]]
        finally:
            release.set()
            await stream.aclose()

    asyncio.run(run())


def test_custom_phase_payload_is_restricted_to_known_fields_and_values():
    async def items():
        for item in [
            "arbitrary text", {"type": "other", "phase": "selecting_tools"},
            {"type": "run_phase", "phase": "failed"},
            {"type": "run_phase", "phase": ["selecting_tools"]},
            {"type": "run_phase", "phase": "selecting_tools", "content": "untrusted"},
            {"type": "run_phase", "phase": "waiting_model"},
        ]:
            yield item

    async def run():
        output = asyncio.Queue()
        await _consume_run_phases(SimpleNamespace(extensions={"custom": items()}), output)
        return list(output._queue)

    assert asyncio.run(run()) == [
        {"type": "run_phase", "phase": "selecting_tools"},
        {"type": "run_phase", "phase": "waiting_model"},
    ]
