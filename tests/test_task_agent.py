"""真实 Deep Agents 图 + 离线模型：执行允许工具，主动尝试绕过后台权限。"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.backend.task_readonly import TaskReadOnlyBackend
from melonclaw.core import task_agent as task_agent_module
from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.config import Settings
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.core.task_agent import TaskAgentOptions
from melonclaw.core.task_policy import TaskExecutionError, TaskRunControl
from melonclaw.services.task_execution import execute_task_agent


def resolved_model():
    return ResolvedModel(
        profile_id="custom:offline",
        display_name="离线模型",
        source="custom",
        adapter_type="openai",
        provider="offline",
        model_name="offline",
        base_url=None,
        api_key="offline-placeholder",
    )


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def settings_for(tmp_path, **kwargs):
    return Settings(
        workspace_root=tmp_path, data_root=tmp_path / "data", agent_usage_enabled=False, **kwargs
    )


async def build(monkeypatch, tmp_path, model, *, settings=None, control=None, **options):
    monkeypatch.setattr(task_agent_module, "build_chat_model", lambda *a, **k: model)
    settings = settings or settings_for(tmp_path)
    control = control or TaskRunControl(
        settings.agent_model_call_limit, settings.agent_tool_call_limit
    )
    agent = await build_research_agent(
        settings,
        model=resolved_model(),
        workspace_dir=tmp_path / "unused-workspace",
        checkpointer=InMemorySaver(),
        unattended=TaskAgentOptions(control=control, **options),
    )
    assert not (tmp_path / "unused-workspace").exists()
    return agent, control, settings


@pytest.mark.parametrize(
    "forbidden",
    [
        "execute",
        "write_file",
        "edit_file",
        "delete",
        "read_file",
        "ls",
        "glob",
        "grep",
        "ask_user",
        "eval",
        "find_tools",
        "confirm_skill_install",
        "confirm_mcp_install",
        "mcp__readonly_hint",
        "memory_search",
        "create_task",
        "configure_task",
        "web_fetch",
        "unknown_future_tool",
        "query_tasks",
        "prepare_task_change",
        "confirm_task_change",
    ],
)
def test_main_cannot_invoke_forbidden_tools(monkeypatch, tmp_path, forbidden):
    async def run():
        agent, control, settings = await build(
            monkeypatch,
            tmp_path,
            Model(
                responses=[
                    AIMessage(
                        content="", tool_calls=[{"name": forbidden, "args": {}, "id": "bad"}]
                    ),
                ]
            ),
        )
        result = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="尝试调用工具",
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert result.status == "failed", result
        assert result.error_code == "tool_not_allowed"
        assert result.result_is_partial
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_allowed_search_and_child_use_same_restricted_catalog(monkeypatch, tmp_path):
    searches, bindings = [], []

    @tool
    async def internet_search(query: str) -> str:
        """Offline search."""
        searches.append(query)
        return "公开资料内容"

    monkeypatch.setattr(task_agent_module, "build_task_search_tool", lambda key: internet_search)

    class ResearchModel(Model):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible={item.name for item in tools})

        async def _agenerate(self, messages, *, visible, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            bindings.append((user.content, visible))
            results = [
                m for m in messages[messages.index(user) + 1 :] if isinstance(m, ToolMessage)
            ]
            if results:
                reply = AIMessage(content="报告：已有来源支持的分析。")
            elif user.content == "委派研究":
                reply = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "id": "delegate",
                            "args": {
                                "description": "子任务搜索",
                                "subagent_type": "general-purpose",
                            },
                        }
                    ],
                )
            else:
                reply = AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "internet_search", "id": "search", "args": {"query": user.content}}
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=reply)])

    async def run():
        settings = settings_for(tmp_path, deepseek_api_key="offline-placeholder")
        agent, control, _ = await build(
            monkeypatch,
            tmp_path,
            ResearchModel(responses=[]),
            settings=settings,
            search_authorized=True,
        )
        result = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="委派研究",
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert result.status == "succeeded", result
        assert result.result_markdown == "报告：已有来源支持的分析。"
        assert searches == ["子任务搜索"]
        assert bindings[0][1] == {"task", "internet_search", "write_todos"}
        child = [names for user, names in bindings if user == "子任务搜索"]
        assert child and all(names == {"internet_search", "write_todos"} for names in child)
        assert not result.result_is_partial

    asyncio.run(run())


@pytest.mark.parametrize(
    "forbidden", ["execute", "write_file", "eval", "find_tools", "task", "ask_user"]
)
def test_child_rejects_forbidden_calls(monkeypatch, tmp_path, forbidden):
    class ChildAttack(Model):
        async def _agenerate(self, messages, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            name, args = ("task", {"description": "child", "subagent_type": "general-purpose"})
            if user.content == "child":
                name, args = forbidden, {}
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {"name": name, "args": args, "id": "call"},
                            ],
                        )
                    )
                ]
            )

    async def run():
        agent, control, settings = await build(monkeypatch, tmp_path, ChildAttack(responses=[]))
        outcome = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="parent",
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert outcome.status == "failed"
        assert outcome.error_code == "tool_not_allowed", outcome

    asyncio.run(run())


def test_batch_with_forbidden_call_executes_no_search(monkeypatch, tmp_path):
    searches = []

    @tool
    async def internet_search(query: str) -> str:
        """Offline search."""
        searches.append(query)
        return "result"

    monkeypatch.setattr(task_agent_module, "build_task_search_tool", lambda key: internet_search)

    async def run():
        agent, _, _ = await build(
            monkeypatch,
            tmp_path,
            Model(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "internet_search", "args": {"query": "safe"}, "id": "safe"},
                            {"name": "execute", "args": {"command": "denied"}, "id": "bad"},
                        ],
                    )
                ]
            ),
            settings=settings_for(tmp_path, deepseek_api_key="offline"),
            search_authorized=True,
        )
        with pytest.raises(TaskExecutionError, match="不支持"):
            await agent.ainvoke(
                {"messages": [HumanMessage(content="test")]},
                {"configurable": {"thread_id": "task-run:test"}},
            )
        assert not searches

    asyncio.run(run())


def test_stop_and_shared_budget_between_main_and_child(monkeypatch, tmp_path):
    async def run():
        control = TaskRunControl(model_limit=1, tool_limit=3)
        agent, _, settings = await build(
            monkeypatch,
            tmp_path,
            Model(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "task",
                                "args": {
                                    "description": "child",
                                    "subagent_type": "general-purpose",
                                },
                                "id": "sub",
                            },
                        ],
                    )
                ]
            ),
            control=control,
        )
        outcome = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="parent",
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert outcome.error_code == "model_limit", outcome
        control.stop()
        with pytest.raises(TaskExecutionError, match="停止"):
            control.consume("tool")

    asyncio.run(run())


def test_file_backend_denies_all_operations():
    backend = TaskReadOnlyBackend()
    assert not hasattr(backend, "execute")
    for response in [
        backend.read("/etc/passwd"),
        backend.ls("/"),
        backend.glob("*"),
        backend.grep("password"),
        backend.write("/x", "content"),
        backend.edit("/x", "before", "after"),
        backend.delete("/x"),
    ]:
        assert response.error
    assert backend.download_files(["/memories/private"])[0].error == "permission_denied"
    assert backend.upload_files([("/x", b"content")])[0].error == "permission_denied"


def test_search_requires_current_authorization_and_configured_key(monkeypatch, tmp_path):
    async def run():
        for authorized, key in [(True, ""), (False, "offline")]:
            agent, control, settings = await build(
                monkeypatch,
                tmp_path,
                Model(
                    responses=[
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "internet_search", "args": {"query": "x"}, "id": "s"}
                            ],
                        ),
                    ]
                ),
                settings=settings_for(tmp_path, deepseek_api_key=key),
                search_authorized=authorized,
            )
            outcome = await execute_task_agent(
                agent,
                run_id=uuid4(),
                instruction="search",
                context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
                control=control,
                settings=settings,
            )
            assert outcome.error_code == "tool_not_allowed"

    asyncio.run(run())


def test_context_and_assembly_reject_chat_capabilities(monkeypatch, tmp_path):
    async def run():
        settings = settings_for(tmp_path)
        with pytest.raises(ValueError, match="不能混入"):
            await build_research_agent(
                settings,
                model=resolved_model(),
                workspace_dir=tmp_path,
                checkpointer=InMemorySaver(),
                mcp_servers={"untrusted": {}},
                unattended=TaskAgentOptions(TaskRunControl(10, 10)),
            )
        context = AgentContext(user_id="u", tenant_id="t", tenant_name="租户")
        for modified in [
            replace(context, memory_enabled=True),
            replace(context, conversation_id="chat"),
        ]:
            with pytest.raises(ValueError, match="上下文"):
                await execute_task_agent(
                    None,
                    run_id=uuid4(),
                    instruction="test",
                    context=modified,
                    control=TaskRunControl(10, 10),
                    settings=settings,
                )

    asyncio.run(run())


def test_context_limit_does_not_offload_or_summarize(monkeypatch, tmp_path):
    calls = []

    class SmallModel(Model):
        async def _agenerate(self, messages, **kwargs):
            calls.append(1)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unexpected"))])

    async def run():
        monkeypatch.setattr(
            task_agent_module, "build_chat_model", lambda *a, **k: SmallModel(responses=[])
        )
        model = replace(resolved_model(), context_window=5000)
        settings = settings_for(tmp_path, agent_output_reserve=500, agent_summary_keep_tokens=500)
        control = TaskRunControl(10, 10)
        agent = await build_research_agent(
            settings,
            model=model,
            workspace_dir=tmp_path,
            checkpointer=InMemorySaver(),
            unattended=TaskAgentOptions(control),
        )
        result = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="研" * 12000,
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert result.error_code == "context_limit", result
        assert not calls

    asyncio.run(run())


def test_task_prompt_has_beijing_execution_and_schedule_time(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from langchain_core.messages import SystemMessage

    from melonclaw.output.content import content_to_text

    prompts = []

    class Capture(Model):
        async def _agenerate(self, messages, **kwargs):
            prompts.extend(
                content_to_text(message.content)
                for message in messages
                if isinstance(message, SystemMessage)
            )
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="有明确日期的报告"))]
            )

    async def run():
        agent, control, settings = await build(
            monkeypatch,
            tmp_path,
            Capture(responses=[]),
            execution_at=datetime(2026, 10, 14, 1, 0, tzinfo=UTC),
            scheduled_for=datetime(2026, 10, 14, 0, 30, tzinfo=UTC),
        )
        outcome = await execute_task_agent(
            agent,
            run_id=uuid4(),
            instruction="整理今日资料",
            context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
            control=control,
            settings=settings,
        )
        assert outcome.status == "succeeded"
        assert any(
            "2026-10-14T09:00:00+08:00" in prompt and "2026-10-14T08:30:00+08:00" in prompt
            for prompt in prompts
        )

    asyncio.run(run())


