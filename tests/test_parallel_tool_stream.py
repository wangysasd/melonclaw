"""并行工具真实完成顺序与审批前的声明状态。"""

import asyncio

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from melonclaw.output.events import iter_research_events


def test_fast_tool_result_does_not_wait_for_slow_tool():
    async def run():
        release = asyncio.Event()
        both_started = [asyncio.Event(), asyncio.Event()]

        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

            async def _astream(self, messages, **kwargs):
                if any(isinstance(m, ToolMessage) for m in messages):
                    yield ChatGenerationChunk(message=AIMessageChunk(content="完成"))
                else:
                    yield ChatGenerationChunk(message=AIMessageChunk(content="并行查询", tool_call_chunks=[
                        {"id": "slow", "name": "slow_read", "args": "{}", "index": 0},
                        {"id": "fast", "name": "fast_read", "args": "{}", "index": 1},
                    ]))

        @tool
        async def slow_read() -> str:
            """Read offline slow result."""
            both_started[0].set()
            await release.wait()
            return "slow"

        @tool
        async def fast_read() -> str:
            """Read offline fast result."""
            both_started[1].set()
            return "fast"

        agent = create_agent(Model(responses=[]), tools=[slow_read, fast_read])
        stream = iter_research_events(agent, {"messages": [{"role": "user", "content": "test"}]}, {})
        events = []
        try:
            async with asyncio.timeout(5):
                async for event in stream:
                    events.append(event)
                    if event["type"] == "assistant_tool_result":
                        assert event["call_id"] == "fast"
                        break
            assert all(e.is_set() for e in both_started)
            assert not release.is_set()
            release.set()
            async for event in stream:
                events.append(event)
            declared = [e["call"] for e in events if e["type"] == "assistant_tool_call" and e["call"]["status"] == "queued"]
            assert declared and all("started_at" not in e for e in declared)
            results = [e["call_id"] for e in events if e["type"] == "assistant_tool_result"]
            assert results == ["fast", "slow"]
        finally:
            release.set()
            await stream.aclose()
    asyncio.run(run())
