"""真实图验证按需选择、跨消息工具池、恢复、隔离与并行预算。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from melonclaw.core.tool_catalog import append_requests
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.tool.tool_discovery import build_tool_discovery


def make_agent(*, fail=False, interrupt=False, pool_size=2):
    selections, bindings = [], []

    class Selector(FakeMessagesListChatModel):
        def with_structured_output(self, schema, **kwargs):
            assert schema["properties"]["tools"]

            async def select(messages, config):
                selections.append(messages[-1].content)
                assert "nostream" in config["tags"]
                if fail:
                    await asyncio.Event().wait()
                name = "second_read" if "second" in messages[-1].content else "first_read"
                return {"tools": [name]}
            return RunnableLambda(select)

    class Model(FakeMessagesListChatModel):
        active: set = set()

        def bind_tools(self, tools, **kwargs):
            self.active = {t.name for t in tools}
            bindings.append(self.active)
            return self

        async def _agenerate(self, messages, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            desired = "second_read" if "second" in user.content else "first_read"
            if user.content == "hello" or (fail and any(m.type == "tool" for m in messages)):
                self.responses = [AIMessage(content="回答")]
            elif desired in self.active:
                self.responses = [AIMessage(content="使用现有工具回答")]
            else:
                self.responses = [AIMessage(content="", tool_calls=[{
                    "name": "find_tools", "args": {"query": desired}, "id": f"find-{user.id}",
                }])]
            return await super()._agenerate(messages, **kwargs)

    @tool
    def first_read() -> str:
        """Read first offline result."""
        return "first"

    @tool
    def second_read() -> str:
        """Read second offline result."""
        return "second"

    catalog = [first_read, second_read]
    middleware = ToolPoolMiddleware(model=Selector(responses=[]), catalog_tools=catalog,
        pool_size=pool_size, timeout_seconds=0.02 if fail else 1)
    agent = create_agent(Model(responses=[]), tools=[*catalog, build_tool_discovery()],
        middleware=[middleware], checkpointer=InMemorySaver(),
        interrupt_before=["tools"] if interrupt else None)
    return agent, selections, bindings, middleware


async def send(agent, content, *, thread="one", message_id="u1"):
    config = {"configurable": {"thread_id": thread}}
    await agent.ainvoke({"messages": [HumanMessage(id=message_id, content=content)]}, config)
    return (await agent.aget_state(config)).values


def test_no_selection_for_chat_and_pool_reused_across_messages():
    async def run():
        agent, calls, bindings, _ = make_agent()
        await send(agent, "hello")
        assert calls == [] and bindings == [{"find_tools"}]
        result = await send(agent, "first", message_id="u2")
        assert len(calls) == 1 and result["tool_pool"]["names"] == ["first_read"]
        await send(agent, "first again", message_id="u3")
        assert len(calls) == 1
        result = await send(agent, "second", message_id="u4")
        assert len(calls) == 2 and result["tool_pool"]["names"] == ["first_read", "second_read"]
        assert "second_read" in calls[-1] and "second" in calls[-1]
    asyncio.run(run())


def test_concurrent_conversations_do_not_share_pool():
    async def run():
        agent, calls, _, _ = make_agent()
        # 同一个 Agent 实例；模型桩避免共享绑定状态竞态，真实状态来自各自 Checkpoint。
        await send(agent, "first", thread="a")
        result = await send(agent, "hello", thread="b")
        assert result["tool_pool"]["names"] == [] and len(calls) == 1
        results = await asyncio.gather(send(agent, "hello", thread="a", message_id="u2"),
                                       send(agent, "hello", thread="b", message_id="u2"))
        assert [r["tool_pool"]["names"] for r in results] == [["first_read"], []]
    asyncio.run(run())


def test_approval_resume_preserves_processed_requests():
    async def run():
        agent, calls, _, _ = make_agent(interrupt=True)
        await send(agent, "first")
        assert calls == []
        config = {"configurable": {"thread_id": "one"}}
        await agent.ainvoke(None, config)
        assert len(calls) == 1
        await agent.ainvoke({"messages": []}, config)
        assert len(calls) == 1
    asyncio.run(run())


def test_timeout_retains_pool_and_does_not_retry_automatically():
    async def run():
        agent, calls, _, _ = make_agent(fail=True)
        result = await send(agent, "first")
        assert len(calls) == 1 and result["tool_pool"]["outcome"] == "degraded"
        await agent.ainvoke({"messages": []}, {"configurable": {"thread_id": "one"}})
        assert len(calls) == 1
    asyncio.run(run())


def test_changed_catalog_clears_pool_without_reselection():
    async def run():
        agent, calls, _, middleware = make_agent()
        await send(agent, "first")
        middleware.fingerprint = "changed-authorized-catalog"
        result = await send(agent, "hello", message_id="u2")
        assert result["tool_pool"]["names"] == [] and len(calls) == 1
    asyncio.run(run())


def test_pool_capacity_evicts_oldest_and_lru_tracks_used_tools():
    async def run():
        agent, _, _, _ = make_agent(pool_size=1)
        await send(agent, "first")
        result = await send(agent, "second", message_id="u2")
        assert result["tool_pool"]["names"] == ["second_read"]
    asyncio.run(run())


def test_request_validation_and_parallel_budget():
    finder = build_tool_discovery()
    for query in ["", "x" * 513]:
        with pytest.raises(ValidationError):
            finder.tool_call_schema.model_validate({"query": query})
    runtime = SimpleNamespace(state={"messages": [HumanMessage(id="u", content="test")],
        "tool_requests": [{"turn_id": "u", "id": str(i), "query": "read"} for i in range(4)]},
        context=None, tool_call_id="d")
    with pytest.raises(ValueError):
        finder.func(query="   ", runtime=runtime)
    result = finder.func(query="read", runtime=runtime)
    assert result.update["tool_requests"] == []
    assert json.loads(result.update["messages"][0].content)["status"] == "exhausted"

    async def run():
        agent, calls, _, _ = make_agent()
        config = {"configurable": {"thread_id": "one"}}
        await send(agent, "hello")
        records = append_requests([], [{"turn_id": "u1", "id": str(i), "query": "first"} for i in range(10)])
        await agent.aupdate_state(config, {"tool_requests": records})
        await agent.ainvoke({"messages": []}, config)
        result = (await agent.aget_state(config)).values
        assert len(calls) == 1
        assert result["tool_pool"]["processed"] == [str(i) for i in range(4)]
    asyncio.run(run())
