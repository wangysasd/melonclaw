"""真实 OpenAI-compatible 协议验证官方结构化选择与父子状态边界。"""

import asyncio
import json

import httpx
from deepagents import create_deep_agent
from deepagents.middleware._state import private_state_field_names
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core.chat_model import ProviderChatOpenAI
from melonclaw.core.tool_catalog import CatalogSelectionState
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.tool.tool_discovery import build_tool_discovery


def test_official_structured_selection_receives_missing_capability_and_reuses_pool():
    async def run():
        calls = []

        @tool
        def price_history() -> str:
            """Read historical prices."""
            return "offline"

        def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            assert body["messages"][-1]["content"] == "需要补充的工具能力：\n历史股价"
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(200, json={
                "id": "selection", "object": "chat.completion", "model": "offline",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": '{"tools":["price_history"]}',
                }}],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ProviderChatOpenAI(model="offline", api_key="offline-placeholder",
                base_url="https://offline.invalid/v1", http_async_client=client)
            middleware = ToolPoolMiddleware(model=model, catalog_tools=[price_history])
            from types import SimpleNamespace
            runtime = SimpleNamespace(context=None, stream_writer=lambda event: None)
            state = {"messages": [HumanMessage(id="u", content="一个复杂任务")],
                     "tool_requests": [{"id": "find", "turn_id": "u", "query": "历史股价"}]}
            update = await middleware.abefore_model(state, runtime)
            state["tool_pool"] = update["tool_pool"]
            assert state["tool_pool"]["names"] == ["price_history"]
            await middleware.abefore_model(state, runtime)
            assert len(calls) == 1
    asyncio.run(run())


def test_pool_state_is_private_and_explicit_subagent_can_expand():
    assert {"tool_pool", "tool_requests"} <= private_state_field_names(CatalogSelectionState)
    # 编译真实 Deep Agents 主/子图，验证 middleware 与请求工具可同时装配。
    @tool
    def read_price() -> str:
        """Read prices."""
        return "offline"

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    model = FakeMessagesListChatModel(responses=[])
    pool = ToolPoolMiddleware(model=model, catalog_tools=[read_price])
    agent = create_deep_agent(model=model, tools=[read_price, build_tool_discovery()],
        middleware=[pool], checkpointer=InMemorySaver(),
        subagents=[{"name": "general-purpose", "description": "General helper", "system_prompt": "Help",
                    "tools": [read_price, build_tool_discovery()], "middleware": [pool]}])
    assert "ToolPoolMiddleware.before_model" in agent.nodes


def test_child_discovery_does_not_inherit_or_overwrite_parent_pool():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.runnables import RunnableLambda

    async def run():
        bindings, selections = [], []

        @tool
        def parent_read() -> str:
            """Read parent data."""
            return "parent"

        @tool
        def child_read() -> str:
            """Read child data."""
            return "child"

        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self.bind(visible_names=[t.name for t in tools])

            def with_structured_output(self, schema, **kwargs):
                async def select(messages):
                    selections.append(messages[-1].content)
                    return {"tools": ["child_read"]}
                return RunnableLambda(select)

            async def _agenerate(self, messages, *, visible_names, **kwargs):
                child = "child marker" in messages[0].content
                bindings.append((child, set(visible_names)))
                if child and "child_read" not in visible_names:
                    message = AIMessage(content="", tool_calls=[{
                        "name": "find_tools", "args": {"query": "child_read"}, "id": "child-find"}])
                elif child or any(isinstance(m, ToolMessage) and m.name == "task" for m in messages):
                    message = AIMessage(content="完成")
                else:
                    message = AIMessage(content="", tool_calls=[{
                        "name": "task", "args": {"description": "child task", "subagent_type": "researcher"},
                        "id": "delegate"}])
                return ChatResult(generations=[ChatGeneration(message=message)])

        model = Model(responses=[])
        catalog = [parent_read, child_read]
        pool = ToolPoolMiddleware(model=model, catalog_tools=catalog)
        tools = [*catalog, build_tool_discovery()]
        agent = create_deep_agent(model=model, system_prompt="parent marker", tools=tools,
            middleware=[pool], checkpointer=InMemorySaver(),
            subagents=[{"name": "researcher", "description": "Research", "system_prompt": "child marker",
                        "tools": tools, "middleware": [pool]}])
        config = {"configurable": {"thread_id": "parent"}}
        await agent.aupdate_state(config, {
            "messages": [HumanMessage(id="u", content="delegate")],
            "tool_pool": {"fingerprint": pool.fingerprint, "turn_id": "u", "names": ["parent_read"],
                          "processed": [], "outcome": "selected"},
        }, as_node="ToolPoolMiddleware.before_model")
        await agent.ainvoke({"messages": [HumanMessage(id="u", content="delegate")]}, config)
        assert (await agent.aget_state(config)).values["tool_pool"]["names"] == ["parent_read"]
        child_bindings = [names for child, names in bindings if child]
        assert "parent_read" not in child_bindings[0]
        assert "child_read" not in child_bindings[0] and "child_read" in child_bindings[-1]
        assert len(selections) == 1 and "child_read" in selections[0]
    asyncio.run(run())
