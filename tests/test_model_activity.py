"""真实 v3 协议下，在模型响应完成前收到正文和文件生成状态。"""

import asyncio

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from melonclaw.output.events import iter_research_events


def test_file_generation_is_visible_before_model_finishes_without_executing_tool():
    async def run():
        release = asyncio.Event()
        emitted = []

        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

            async def _astream(self, *args, **kwargs):
                yield ChatGenerationChunk(message=AIMessageChunk(content="<thi"))
                yield ChatGenerationChunk(message=AIMessageChunk(content="nk>private reasoning"))
                yield ChatGenerationChunk(message=AIMessageChunk(content="</think>开始生成"))
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"id": "call-1", "name": "write_file", "args": '{"content":"', "index": 0},
                ]))
                await release.wait()
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"id": None, "name": None, "args": 'private document"}', "index": 0},
                ]))

        @tool
        def write_file(content: str) -> str:
            """Write a file (must not execute in this test)."""
            raise AssertionError("No file tool may run before approval")

        agent = create_agent(Model(responses=[]), tools=[write_file], interrupt_before=["tools"])
        stream = iter_research_events(agent, {"messages": [{"role": "user", "content": "test"}]}, {})
        try:
            async with asyncio.timeout(5):
                async for event in stream:
                    emitted.append(event)
                    if event == {"type": "run_phase", "phase": "preparing_file"}:
                        break
            assert any(e["type"] == "assistant_text_delta" for e in emitted)
            assert not any(e["type"] == "assistant_tool_call" for e in emitted)
            assert "private document" not in str(emitted)
            text = "".join(e["delta"] for e in emitted if e["type"] == "assistant_text_delta")
            assert text == "<think>private reasoning</think>开始生成"
            release.set()
            async for event in stream:
                emitted.append(event)
            assert any(e["type"] == "assistant_step_completed" for e in emitted)
        finally:
            release.set()
            await stream.aclose()

    asyncio.run(run())
