"""惰性 QuickJS 解释器：未调用 eval 不启动虚拟机/不写空堆快照，调用时保留 PTC 与跨轮状态。"""

from __future__ import annotations

import asyncio

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core.interpreter import LazyCodeInterpreterMiddleware, build_interpreter_middleware


class RecordingToolModel(FakeMessagesListChatModel):
    def __init__(self, responses):
        super().__init__(responses=responses)
        self._recorded_system_prompts: list[str] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if messages and getattr(messages[0], "type", None) == "system":
            self._recorded_system_prompts.append(str(messages[0].content))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_normal_turn_without_eval_skips_repl_boot_and_snapshot():
    interpreter = build_interpreter_middleware()
    assert isinstance(interpreter, LazyCodeInterpreterMiddleware)
    checkpointer = InMemorySaver()
    model = RecordingToolModel(responses=[AIMessage(content="ok"), AIMessage(content="ok2")])

    @tool
    def internet_search(query: str) -> str:
        """搜索公开网页。"""
        return f"found:{query}"

    agent = create_deep_agent(
        model=model,
        tools=[internet_search],
        middleware=[interpreter],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "lazy-no-eval"}}

    async def run():
        await agent.ainvoke({"messages": [{"role": "user", "content": "hello"}]}, config)
        assert interpreter._registry.get_if_exists("lazy-no-eval") is None
        state = await agent.aget_state(config)
        assert not state.values.get("_quickjs_snapshot_payload")
        assert any("internetSearch" in p for p in model._recorded_system_prompts)

        await agent.ainvoke({"messages": [{"role": "user", "content": "again"}]}, config)
        state2 = await agent.aget_state(config)
        assert not state2.values.get("_quickjs_snapshot_payload")

    asyncio.run(run())


def test_eval_lazily_boots_repl_runs_ptc_and_persists_thread_state():
    interpreter = build_interpreter_middleware()
    checkpointer = InMemorySaver()
    ptc_calls: list[str] = []

    @tool
    async def internet_search(query: str) -> str:
        """搜索公开网页。"""
        ptc_calls.append(query)
        return f"result:{query}"

    model = RecordingToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "eval",
                        "args": {"code": "globalThis.saved = await tools.internetSearch({query: 'hello'}); globalThis.saved"},
                    }
                ],
            ),
            AIMessage(content="turn1-done"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-2",
                        "name": "eval",
                        "args": {"code": "globalThis.saved + ':turn2'"},
                    }
                ],
            ),
            AIMessage(content="turn2-done"),
        ]
    )

    agent = create_deep_agent(
        model=model,
        tools=[internet_search],
        middleware=[interpreter],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "lazy-with-eval"}}

    async def run():
        first = await agent.ainvoke({"messages": [{"role": "user", "content": "run js"}]}, config)
        tool_msgs = [m for m in first["messages"] if getattr(m, "type", None) == "tool"]
        assert ptc_calls == ["hello"]
        assert any("result:hello" in str(m.content) for m in tool_msgs)
        state1 = await agent.aget_state(config)
        assert isinstance(state1.values.get("_quickjs_snapshot_payload"), bytes)

        second = await agent.ainvoke({"messages": [{"role": "user", "content": "read var"}]}, config)
        tool_msgs2 = [m for m in second["messages"] if getattr(m, "type", None) == "tool"]
        assert any("result:hello:turn2" in str(m.content) for m in tool_msgs2)

    asyncio.run(run())
