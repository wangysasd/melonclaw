"""完整 Agent 图验证：空名称使整批失败，不进入工具纠错循环。"""

import asyncio

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from melonclaw.middleware.tool_name_guard import (
    EmptyToolNameError,
    ToolNameGuardMiddleware,
)
from melonclaw.output.events import iter_research_events


class ToolModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        self.calls += 1
        return super()._generate(*args, **kwargs)


def make_agent(name):
    executed = []

    @tool
    def record() -> str:
        """Record a test invocation."""
        executed.append(True)
        return "ok"

    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "record", "args": {}, "id": "valid"},
            {"name": name, "args": {}, "id": "candidate"},
        ]),
        AIMessage(content="done"),
    ])
    agent = create_agent(model, tools=[record], middleware=[ToolNameGuardMiddleware()])
    return agent, model, executed


@pytest.mark.parametrize("name", ["", " \t\n"])
@pytest.mark.parametrize("mode", ["sync", "async", "v3", "projection"])
def test_empty_name_aborts_entire_batch(name, mode):
    agent, model, executed = make_agent(name)
    inputs = {"messages": [{"role": "user", "content": "run"}]}

    async def run():
        if mode == "projection":
            async for _ in iter_research_events(agent, inputs, {}):
                pass
        elif mode == "v3":
            stream = await agent.astream_events(inputs, version="v3")
            await stream.output()
        else:
            await agent.ainvoke(inputs)

    with pytest.raises(EmptyToolNameError, match="该批工具未执行"):
        if mode == "sync":
            agent.invoke(inputs)
        else:
            asyncio.run(run())
    assert executed == []
    assert model.calls == 1


def test_valid_batch_and_text_response_are_allowed():
    agent, model, executed = make_agent("record")
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "run"}]}))
    assert result["messages"][-1].content == "done"
    assert len(executed) == 2
    assert model.calls == 2


@pytest.mark.parametrize("name", [None, "", " "])
def test_invalid_tool_call_without_name_is_rejected(name):
    message = AIMessage(content="", invalid_tool_calls=[
        {"name": name, "args": "private invalid args", "id": "bad", "error": "bad json"},
    ])
    with pytest.raises(EmptyToolNameError) as exc:
        ToolNameGuardMiddleware._validate(message)
    assert "private" not in str(exc.value)
