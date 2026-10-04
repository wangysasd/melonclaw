"""同轮复用、显式扩展、跨会话与恢复隔离，使用真实框架状态更新。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from melonclaw.core.tool_catalog import append_discoveries
from melonclaw.middleware.tool_selection import CatalogToolSelectorMiddleware
from melonclaw.output.events import iter_research_events
from melonclaw.tool.tool_discovery import build_tool_discovery


def make_agent(*, interrupt=False, timeout=False):
    selections = []
    bindings = []

    class Selector(FakeMessagesListChatModel):
        async def _agenerate(self, messages, **kwargs):
            selections.append(messages[-1].content)
            if timeout:
                await asyncio.Event().wait()
            return await super()._agenerate(messages, **kwargs)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            bindings.append({item.name for item in tools})
            return self

        async def _astream(self, messages, **kwargs):
            user_index = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
            results = [m.name for m in messages[user_index:] if isinstance(m, ToolMessage)]
            if "second_read" in results:
                yield ChatGenerationChunk(message=AIMessageChunk(content="完成"))
                return
            if "find_tools" in results:
                name, args = "second_read", "{}"
            elif "first_read" in results or timeout:
                name, args = "find_tools", '{"query":"second_read"}'
            else:
                name, args = "first_read", "{}"
            yield ChatGenerationChunk(message=AIMessageChunk(content="继续", tool_call_chunks=[
                {"id": f"{messages[user_index].id}-{name}", "name": name, "args": args, "index": 0},
            ]))

    @tool
    def first_read() -> str:
        """Read first offline result."""
        return "first"

    @tool
    def second_read() -> str:
        """Read second offline result."""
        return "second"

    catalog = [first_read, second_read]
    agent = create_agent(
        Model(responses=[]), tools=[*catalog, build_tool_discovery(catalog)],
        middleware=[CatalogToolSelectorMiddleware(
            model=Selector(responses=[AIMessage(content='{"tools":["first_read"]}')]),
            catalog_tool_names={"first_read", "second_read"}, max_tools=1, timeout_seconds=0.02 if timeout else 1,
        )],
        checkpointer=InMemorySaver(), interrupt_before=["tools"] if interrupt else None,
    )
    return agent, selections, bindings


async def collect(agent, content, thread):
    config = {"configurable": {"thread_id": thread}}
    return [event async for event in iter_research_events(agent, content, config)]


def test_selection_is_reused_and_discovery_activates_an_omitted_tool():
    async def run():
        agent, selections, bindings = make_agent()
        await collect(agent, {"messages": [HumanMessage(id="u1", content="one")]}, "one")
        assert selections == ["one"]
        assert "second_read" not in bindings[0]
        assert "second_read" in bindings[-1]
        state = (await agent.aget_state({"configurable": {"thread_id": "one"}})).values
        assert state["tool_discoveries"] == [{"turn_id": "u1", "names": ["second_read"]}]
        assert state["tool_selection"]["names"] == ["first_read"]
        await collect(agent, {"messages": [HumanMessage(id="u2", content="two")]}, "one")
        assert selections == ["one", "two"]
        state = (await agent.aget_state({"configurable": {"thread_id": "one"}})).values
        assert state["tool_discoveries"] == [{"turn_id": "u2", "names": ["second_read"]}]
    asyncio.run(run())


def test_shared_agent_keeps_concurrent_conversations_separate():
    async def run():
        agent, selections, _ = make_agent()
        await asyncio.gather(
            collect(agent, {"messages": [HumanMessage(id="a", content="A")]}, "a"),
            collect(agent, {"messages": [HumanMessage(id="b", content="B")]}, "b"),
        )
        assert sorted(selections) == ["A", "B"]
        states = await asyncio.gather(*(agent.aget_state({"configurable": {"thread_id": name}}) for name in ["a", "b"]))
        assert [s.values["tool_selection"]["turn_id"] for s in states] == ["a", "b"]
    asyncio.run(run())


def test_approval_pause_keeps_selection_in_checkpoint():
    async def run():
        agent, selections, _ = make_agent(interrupt=True)
        await collect(agent, {"messages": [HumanMessage(id="u1", content="one")]}, "one")
        assert selections == ["one"]
        await collect(agent, None, "one")
        assert selections == ["one"]
        state = await agent.aget_state({"configurable": {"thread_id": "one"}})
        assert state.values["tool_selection"]["turn_id"] == "u1"
    asyncio.run(run())


def test_selector_timeout_degrades_once_and_discovery_still_works():
    async def run():
        agent, selections, bindings = make_agent(timeout=True)
        async with asyncio.timeout(5):
            events = await collect(agent, {"messages": [HumanMessage(id="u1", content="one")]}, "one")
        assert selections == ["one"]
        assert bindings[0] == {"find_tools"}
        assert "second_read" in bindings[-1]
        assert any(event.get("outcome") == "degraded" for event in events)
    asyncio.run(run())


def test_stale_catalog_fingerprint_triggers_reselection_in_same_turn():
    async def run():
        agent, selections, _ = make_agent()
        config = {"configurable": {"thread_id": "one"}}
        await collect(agent, {"messages": [HumanMessage(id="u1", content="one")]}, "one")
        selection = dict((await agent.aget_state(config)).values["tool_selection"])
        selection["fingerprint"] = "obsolete-catalog"
        await agent.aupdate_state(config, {"tool_selection": selection})
        await collect(agent, {"messages": []}, "one")
        assert selections == ["one", "one"]
        assert (await agent.aget_state(config)).values["tool_discoveries"] == []
    asyncio.run(run())


def test_discovery_input_limits_exhaustion_and_shared_parallel_budget():
    @tool
    def allowed_read() -> str:
        """Read allowed local data."""
        return "ok"

    finder = build_tool_discovery([allowed_read])
    for query in ["", "x" * 513]:
        with pytest.raises(ValidationError):
            finder.tool_call_schema.model_validate({"query": query})
    runtime = SimpleNamespace(state={"messages": [HumanMessage(id="u", content="test")],
        "tool_discoveries": [{"turn_id": "u", "names": []}] * 4}, context=None, tool_call_id="d")
    result = finder.func(query="allowed_read", runtime=runtime)
    assert result.update["tool_discoveries"] == []
    assert json.loads(result.update["messages"][0].content)["remaining_searches"] == 0
    result = []
    for index in range(10):
        result = append_discoveries(result, [{"turn_id": "u", "names": [f"t{index * 8 + n}" for n in range(8)]}])
    assert len(result) == 4
    assert sum(len(item["names"]) for item in result) == 16
