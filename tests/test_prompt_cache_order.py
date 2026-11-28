"""前缀缓存顺序回归测试：静态内容在前，易变/按需内容在后。"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core import agent as agent_module
from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.config import Settings
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.core.prompts import MEMORY_GUIDANCE, build_system_prompt
from melonclaw.middleware.tool_selection import ToolPoolMiddleware


def test_build_system_prompt_places_static_guidance_before_mcp_and_date():
    prompt_day1 = build_system_prompt(["srv-a"], current_date=date(2026, 11, 28), memory_enabled=True)
    prompt_day2 = build_system_prompt(["srv-a"], current_date=date(2026, 11, 29), memory_enabled=True)

    assert prompt_day1.index(MEMORY_GUIDANCE) < prompt_day1.index("srv-a")
    assert prompt_day1.index("srv-a") < prompt_day1.index("今天的日期是 2026-11-28")
    assert prompt_day1.endswith("今天的日期是 2026-11-28（以当前本地时区为准）。")
    # 只有末尾日期一行不同，前半段静态指引完全一致
    prefix_len = prompt_day1.index("今天的日期是")
    assert prompt_day1[:prefix_len] == prompt_day2[:prefix_len]


def test_tool_pool_appends_dynamic_tools_after_static_tools():
    @tool
    def internet_search(query: str) -> str:
        """搜索。"""
        return query

    @tool
    def mcp_dynamic_a(x: str) -> str:
        """动态工具 A。"""
        return x

    @tool
    def mcp_dynamic_b(x: str) -> str:
        """动态工具 B。"""
        return x

    @tool
    def find_tools(query: str) -> str:
        """发现工具。"""
        return query

    @tool
    def list_mcp_tools() -> str:
        """列出 MCP。"""
        return ""

    all_tools = [internet_search, mcp_dynamic_a, mcp_dynamic_b, list_mcp_tools, find_tools]
    middleware = ToolPoolMiddleware(
        model=FakeMessagesListChatModel(responses=[]),
        catalog_tools=[mcp_dynamic_a, mcp_dynamic_b],
    )
    runtime = SimpleNamespace(context=SimpleNamespace(knowledge_base_ids=()))

    req_empty = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        tools=all_tools,
        messages=[HumanMessage(content="hi")],
        state={"tool_pool": {"names": []}},
        runtime=runtime,
    )
    req_active = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        tools=all_tools,
        messages=[HumanMessage(content="hi")],
        state={"tool_pool": {"names": ["mcp_dynamic_b", "mcp_dynamic_a"]}},
        runtime=runtime,
    )

    static_names = [t.name for t in middleware.filter_request(req_empty).tools]
    active_names = [t.name for t in middleware.filter_request(req_active).tools]

    assert static_names == ["internet_search", "list_mcp_tools", "find_tools"]
    # 常驻工具前缀顺序保持不变，动态工具追加在末尾且按稳定顺序排列
    assert active_names[: len(static_names)] == static_names
    assert active_names[len(static_names) :] == ["mcp_dynamic_a", "mcp_dynamic_b"]


def test_memory_prompt_is_appended_after_static_interpreter_prompt(monkeypatch, tmp_path):
    recorded_prompts: list[str] = []

    class CaptureModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if messages and getattr(messages[0], "type", None) == "system":
                content = messages[0].content
                text = (
                    "\n".join(b["text"] if isinstance(b, dict) else str(b) for b in content)
                    if isinstance(content, list)
                    else str(content)
                )
                recorded_prompts.append(text)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    fake_memory = SimpleNamespace(
        store=None,
        installation_id="local",
        agent_id="quickstart-research-agent",
        load_prompt=AsyncMock(return_value="<memory_data>\n[user:pref]\n喜欢简洁回答\n</memory_data>"),
    )
    model = CaptureModel(responses=[AIMessage(content="ok")])
    resolved = ResolvedModel(
        profile_id="test:model",
        provider="deepseek",
        model_name="deepseek-chat",
        display_name="Test",
        source="global",
        adapter_type="openai_compatible",
        base_url="https://example.test/v1",
        api_key="fixture-key",
        context_window=64000,
    )
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *a, **kw: model)
    monkeypatch.setattr(agent_module, "build_agent_tools", AsyncMock(return_value=[]))

    async def run():
        agent = await build_research_agent(
            Settings(database_url="postgresql://localhost/test", data_root=tmp_path, workspace_root=tmp_path),
            checkpointer=InMemorySaver(),
            workspace_dir=tmp_path / "ws",
            model=resolved,
            memory_service=fake_memory,
        )
        ctx = AgentContext(
            user_id="alice",
            tenant_id="system",
            tenant_name="系统",
            memory_enabled=True,
        )
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}]},
            config={"configurable": {"thread_id": "mem-order-test"}},
            context=ctx,
        )
        assert recorded_prompts
        prompt = recorded_prompts[0]
        assert "### Interpreter" in prompt
        assert "当前配置的 MCP 服务：" in prompt
        assert "今天的日期是 " in prompt
        assert "<memory_data>" in prompt
        # 静态解释器提示在前，按用户/按天变化的 MCP 清单、日期与长期 Memory 依次排在最末尾
        assert prompt.index("### Interpreter") < prompt.index("当前配置的 MCP 服务：")
        assert prompt.index("当前配置的 MCP 服务：") < prompt.index("今天的日期是 ")
        assert prompt.index("今天的日期是 ") < prompt.index("<memory_data>")
        # QuickJS 提示已精简，不再包含近万字英文长教程
        interpreter_section = prompt[prompt.index("### Interpreter") : prompt.index("当前配置的 MCP 服务：")]
        assert len(interpreter_section) < 2000
        assert "await task({ description, subagentType" in interpreter_section

    asyncio.run(run())


def test_runtime_prompt_tail_updates_date_across_days_on_same_middleware():
    from melonclaw.middleware.runtime_prompt import RuntimePromptTailMiddleware

    current = [date(2026, 11, 28)]
    mw = RuntimePromptTailMiddleware(["srv-b", "srv-a"], date_provider=lambda: current[0])
    seen: list[str] = []

    async def capture(req):
        seen.append(str(req.system_message.content))
        return SimpleNamespace(result=[])

    req = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        tools=[],
        messages=[HumanMessage(content="hi")],
        state={},
        runtime=SimpleNamespace(context=None),
    )

    async def run():
        await mw.awrap_model_call(req, capture)
        current[0] = date(2026, 11, 29)
        await mw.awrap_model_call(req, capture)

    asyncio.run(run())
    assert "srv-a、srv-b" in seen[0]
    assert "2026-11-28" in seen[0]
    assert "2026-11-29" in seen[1]
