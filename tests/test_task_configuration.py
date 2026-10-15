"""对话配置任务：实际工具调用、归属、事务、版本与累计会话。"""

import asyncio
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine
from test_task_agent import Model, resolved_model, settings_for
from test_task_postgres import claim, database_fixture
from test_task_postgres import install_model as install_task_model

from melonclaw.core import agent as agent_module
from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.hitl import build_user_input_resume_command, serialize_pending_user_question
from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
from melonclaw.core.task_configuration import TaskChanges
from melonclaw.core.user_input import USER_INPUT_CAPABILITY
from melonclaw.database.schema import chat_conversations, chat_messages, task_operations, users
from melonclaw.repository import BusinessRepository
from melonclaw.repository.task_history import capture_history, load_history
from melonclaw.services.accounts import AccountService
from melonclaw.services.runtime import ChatRuntime
from melonclaw.services.task_configuration import TaskConfigurationService
from melonclaw.services.tasks import TaskService
from melonclaw.tool.task_configuration import build_task_configuration_tool

pg = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL")
CREATION = {"name": "连载小说", "instruction": "写30集小说，每次一集，主角林舟。",
            "schedule": {"kind": "daily", "local_time": "09:00"}}


def install_model(monkeypatch):
    install_task_model(monkeypatch)

    async def system_default(*args, **kwargs):
        return resolved_model()
    monkeypatch.setattr(TaskService, "resolve_system_default_model", system_default)


def runtime_for(storage, tmp_path):
    async def models(user_id):
        return {"items": [{"id": "custom:offline", "display_name": "离线模型", "available": True,
                           "scope": "global", "is_default": True}]}
    return SimpleNamespace(require_ready=lambda: storage, settings=settings_for(tmp_path), models=models)


async def turn(storage, cid=None, *, user_id="admin", tenant_id="system", project_id=None,
               content="每天早上9点写一集小说，共30集。"):
    if cid is None:
        conversation = await storage.create_conversation(user_id, project_id)
        cid = UUID(conversation["id"])
    cid = UUID(str(cid))
    request_id = str(uuid4())
    pair = await storage.create_message_pair(
        cid, user_id, project_id, request_id, content,
        attachment_ids=[], model_id="custom:offline", model_provider="offline",
        model_name="offline", model_display_name="离线", model_supports_image=False,
        max_attachment_count=4, max_total_bytes=1000,
    )
    return AgentContext(user_id=user_id, tenant_id=tenant_id, tenant_name="测试",
                        conversation_id=str(cid), user_message_id=pair.user_message["id"],
                        request_id=request_id, model_id="custom:offline",
                        project_id=str(project_id) if project_id else "")


async def finish_chat(storage, context):
    async with storage.engine.begin() as conn:
        await conn.execute(update(chat_messages).where(
            chat_messages.c.conversation_id == UUID(context.conversation_id),
            chat_messages.c.request_id == context.request_id, chat_messages.c.role == "assistant",
        ).values(status="completed", content="已处理"))


async def save(provider, context, changes=CREATION, version=0, call_id="save"):
    return await provider.configure(context, call_id, action="save", changes=changes,
                                    expected_version=version)


async def default_models(storage):
    await storage.create_provider_row(provider_key="offline", scope="global", source_type="manual",
        display_name="离线供应商", provider_type="openai_compatible", base_url="https://example.invalid/v1",
        api_key="offline-placeholder", models_endpoint=None, created_by="admin")
    for key, scope, default in [("z-system", "global", True), ("a-chat", "global", False),
                                ("personal", "user", True)]:
        await storage.create_model_row(model_key=key, provider_key="offline", scope=scope,
            source_type="manual", display_name=key, model_name=key, created_by="admin", is_default=default)


def database_runtime(storage, tmp_path):
    return ChatRuntime(settings=settings_for(tmp_path), storage=storage,
                       checkpointer=InMemorySaver(), memory_store=object(), memory_service=object())


@pg
def test_system_default_ignores_chat_and_personal_default_and_replay_survives_switch(tmp_path):
    async def run():
        async with database_fixture() as storage:
            await default_models(storage)
            await storage.upsert_user_provider_key(provider_key="offline", user_id="admin", api_key="personal-placeholder")
            runtime = database_runtime(storage, tmp_path)
            assert (await runtime.models("admin"))["default_model_id"] == "custom:personal"
            provider = TaskConfigurationService(runtime)
            context = replace(await turn(storage), model_id="custom:a-chat")
            inspected = await provider.configure(context, "inspect")
            assert inspected["default_model_id"] == "custom:z-system"
            saved = await save(provider, context)
            assert saved["status"] == "saved", saved
            assert saved["task"]["definition"]["model_id"] == "custom:z-system"
            await storage.set_default_model("a-chat")
            assert await save(provider, context) == saved
            edited = await save(provider, context, {"name": "改名"}, 1, "rename")
            assert edited["task"]["definition"]["model_id"] == "custom:z-system"
            fresh = await turn(storage)
            assert (await save(provider, fresh))["task"]["definition"]["model_id"] == "custom:a-chat"
            explicit = await turn(storage)
            choice = await save(provider, explicit, {**CREATION, "model_id": "custom:personal"})
            assert choice["task"]["definition"]["model_id"] == "custom:personal"
    asyncio.run(run())


@pg
@pytest.mark.parametrize("state", ["missing", "disabled", "no_credentials"])
def test_unavailable_system_default_is_explicit_error_without_model_fallback(tmp_path, state):
    async def run():
        async with database_fixture() as storage:
            await default_models(storage)
            if state == "missing":
                await storage.update_model_row("z-system", is_default=False)
            elif state == "disabled":
                await storage.update_model_row("z-system", enabled=False)
            else:
                await storage.update_provider_row("offline", api_key=None)
            provider = TaskConfigurationService(database_runtime(storage, tmp_path))
            context = replace(await turn(storage), model_id="custom:a-chat")
            inspected = await provider.configure(context, "inspect")
            assert inspected["default_model_id"] is None
            failed = await save(provider, context)
            assert failed["code"] == "system_default_model_unavailable", failed
            assert "系统默认模型" in failed["message"]
            assert (await TaskService(provider.runtime.settings, storage).repository.list_tasks("admin"))["total"] == 0
            if state != "no_credentials":
                explicit = await save(provider, context, {**CREATION, "model_id": "custom:a-chat"})
                assert explicit["status"] == "saved"
                assert explicit["task"]["definition"]["model_id"] == "custom:a-chat"
    asyncio.run(run())


@pg
def test_create_binds_current_chat_without_synthetic_messages_and_replays(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            provider = TaskConfigurationService(runtime_for(storage, tmp_path))
            context = await turn(storage)
            inspected = await provider.configure(context, "inspect")
            assert inspected["version"] == 0 and inspected["task"] is None
            assert inspected["default_model_id"] == "custom:offline"
            assert inspected["now"].endswith("+08:00")
            saved, replay = await asyncio.gather(save(provider, context), save(provider, context))
            assert saved == replay and saved["status"] == "saved"
            assert saved["task"]["conversation_id"] == context.conversation_id
            assert saved["task"]["definition"]["model_id"] == "custom:offline"
            assert saved["task"]["status"] == "enabled"
            assert saved["next_run_at_beijing"].endswith("T09:00:00+08:00")
            async with storage.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(chat_conversations)) == 1
                assert await conn.scalar(select(func.count()).select_from(chat_messages)) == 2
                assert await conn.scalar(select(func.count()).select_from(task_operations)) == 1
            conflict = await save(provider, context, {**CREATION, "name": "不同内容"})
            assert conflict["code"] == "idempotency_conflict"
            # 已结束后的同一调用可恢复保存回执；新的调用不能用旧消息再次写入。
            await finish_chat(storage, context)
            assert await save(provider, context) == saved
            assert (await save(provider, context, {"enabled": False}, 1, "late"))["code"] == "request_finished"
    asyncio.run(run())


@pg
def test_partial_edit_pause_and_future_run_use_new_config_without_changing_snapshot(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            runtime = runtime_for(storage, tmp_path)
            provider, service = TaskConfigurationService(runtime), TaskService(runtime.settings, storage)
            context = await turn(storage)
            original = await save(provider, context)
            tid = UUID(original["task"]["id"])
            old = await service.run("admin", tid, idempotency_key=str(uuid4()), expected_version=1)
            owned = await claim(storage.task_queue)
            changed = await save(provider, context, {"schedule": {"kind": "daily", "local_time": "20:00"}}, 1, "time")
            assert changed["status"] == "saved"
            definition = changed["task"]["definition"]
            assert definition["instruction"] == CREATION["instruction"]
            assert definition["name"] == CREATION["name"] and definition["model_id"] == "custom:offline"
            assert (await service.get_run("admin", UUID(old["id"])))["config_snapshot"]["definition"]["schedule"]["local_time"] == "09:00"
            assert (await save(provider, context, {"name": "过期配置"}, 1, "stale"))["code"] == "version_conflict"
            assert await storage.task_queue.finish(owned, status="succeeded", result="第一集")
            paused = await save(provider, context, {"enabled": False}, 2, "pause")
            assert paused["task"]["status"] == "paused" and paused["next_run_at_beijing"] is None
            resumed = await save(provider, context, {"enabled": True}, 3, "resume")
            assert resumed["task"]["status"] == "enabled"
            upcoming = await service.run("admin", tid, idempotency_key=str(uuid4()), expected_version=4)
            assert upcoming["config_snapshot"]["definition"]["schedule"]["local_time"] == "20:00"
            assert upcoming["config_snapshot"]["history"]["completed_runs"] == 1
    asyncio.run(run())


@pg
def test_followup_edit_keeps_history_and_targets_only_current_conversation(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            provider = TaskConfigurationService(runtime_for(storage, tmp_path))
            context = await turn(storage)
            created = await save(provider, context)
            await finish_chat(storage, context)
            followup = await turn(storage, context.conversation_id, content="后续文风轻松一点。")
            inspected = await provider.configure(followup, "inspect")
            assert inspected["version"] == 1 and inspected["task"]["id"] == created["task"]["id"]
            updated = await save(provider, followup, {"instruction": CREATION["instruction"] + "文风轻松一点。"}, 1)
            assert updated["task"]["conversation_id"] == context.conversation_id
            async with storage.engine.connect() as conn:
                snapshot = await capture_history(conn, "admin", UUID(context.conversation_id))
                rows = await load_history(conn, "admin", snapshot)
            assert [r["content"] for r in rows] == ["每天早上9点写一集小说，共30集。", "已处理", "后续文风轻松一点。"]
            assert not any(r["display_metadata"].get("task_setup") for r in rows)
            another = await turn(storage)
            assert (await provider.configure(another, "read"))["version"] == 0
            # 即使知道其他任务 ID，schema 也不接受它。
            assert (await save(provider, another, {**CREATION, "task_id": created["task"]["id"]}))["code"] == "invalid_configuration"
    asyncio.run(run())


@pg
def test_denies_cross_user_tenant_invalid_source_and_project_chats(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            provider = TaskConfigurationService(runtime_for(storage, tmp_path))
            context = await turn(storage)
            assert (await save(provider, replace(context, user_id="missing")))["status"] == "error"
            await AccountService(storage).save_user("alice", "用户甲", "system", password="test-password")
            assert (await save(provider, replace(context, user_id="alice")))["code"] == "task_not_found"
            assert (await save(provider, replace(context, tenant_id="other")))["code"] == "identity_changed"
            assert (await save(provider, replace(context, user_message_id=str(uuid4()))))["code"] == "invalid_source"
            assert (await save(provider, replace(context, request_id=str(uuid4()))))["code"] == "invalid_source"
            assert (await save(provider, replace(context, project_id=str(uuid4()))))["code"] == "project_task_forbidden"
            project = await storage.create_project("admin", "项目")
            project_context = await turn(storage, project_id=UUID(project["id"]))
            # 即使伪造上下文的 project_id 为空，数据库归属仍然拒绝。
            assert (await save(provider, replace(project_context, project_id="")))["code"] == "project_task_forbidden"
            created = await save(provider, context)
            async with storage.engine.begin() as conn:
                await conn.execute(update(users).where(users.c.user_id == "admin").values(tenant_status="deleted"))
            assert (await save(provider, context))["status"] == "error"
            assert created["status"] == "saved"
    asyncio.run(run())


@pg
def test_invalid_create_and_archived_task_leave_configuration_unchanged(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            runtime = runtime_for(storage, tmp_path)
            provider = TaskConfigurationService(runtime)
            context = await turn(storage)
            for changes in ({"name": "缺要求"}, {**CREATION, "schedule": {"kind": "hourly"}},
                            {**CREATION, "schedule": {"kind": "once", "run_at": "2020-01-01T09:00:00+08:00"}},
                            {**CREATION, "model_id": "invented"}):
                assert (await save(provider, context, changes))["status"] == "error"
                assert (await provider.configure(context, "inspect"))["version"] == 0
            created = await save(provider, context)
            service = TaskService(runtime.settings, storage)
            await service.archive("admin", UUID(created["task"]["id"]), expected_version=1,
                                  idempotency_key=str(uuid4()))
            assert (await save(provider, context, {"enabled": True}, 2, "restore"))["code"] == "task_archived"
    asyncio.run(run())


@pg
def test_pause_with_unavailable_model_and_reject_invalid_resume(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            provider = TaskConfigurationService(runtime_for(storage, tmp_path))
            context = await turn(storage)
            assert (await save(provider, context))["status"] == "saved"

            async def unavailable(*args, **kwargs):
                raise ValueError("模型不可用")
            monkeypatch.setattr(TaskService, "resolve_model", unavailable)
            assert (await save(provider, context, {"enabled": False}, 1, "pause"))["task"]["status"] == "paused"
            failed = await save(provider, context, {"enabled": True}, 2, "resume")
            assert failed["code"] == "model_unavailable"
            current = await provider.configure(context, "inspect")
            assert current["version"] == 2 and current["task"]["status"] == "paused"
    asyncio.run(run())


@pg
def test_real_model_and_identity_resolution_with_one_connection(tmp_path):
    async def run():
        async with database_fixture() as setup:
            await setup.create_provider_row(provider_key="offline", scope="global", source_type="manual",
                display_name="离线供应商", provider_type="openai_compatible", base_url="https://example.invalid/v1",
                api_key="offline-placeholder", models_endpoint=None, created_by="admin")
            await setup.create_model_row(model_key="offline", provider_key="offline", scope="global",
                source_type="manual", display_name="离线模型", model_name="offline", created_by="admin", is_default=True)
            from sqlalchemy import text

            async with setup.engine.connect() as conn:
                schema = await conn.scalar(text("SELECT current_schema()"))
            engine = create_async_engine(os.environ["MELONCLAW_TEST_DATABASE_URL"],
                pool_size=1, max_overflow=0, pool_timeout=0.5,
                connect_args={"server_settings": {"search_path": schema}})
            try:
                storage = BusinessRepository(SimpleNamespace(engine=engine))
                runtime = ChatRuntime(settings=settings_for(tmp_path), storage=storage,
                                      checkpointer=InMemorySaver(), memory_store=object(),
                                      memory_service=object())
                provider = TaskConfigurationService(runtime)
                context = await turn(storage)
                inspected = await asyncio.wait_for(provider.configure(context, "inspect"), 3)
                assert inspected["status"] == "ready", inspected
                assert inspected["available_models"] == [{"id": "custom:offline", "name": "离线模型"}]
                saved = await asyncio.wait_for(save(provider, context), 3)
                assert saved["status"] == "saved"
                assert "offline-placeholder" not in json.dumps(saved)
                assert (await save(provider, context, {"model_id": "custom:missing"}, 1, "wrong-model"))["code"] == "model_unavailable"
            finally:
                await engine.dispose()
    asyncio.run(run())


class ToolModel(Model):
    bindings: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bindings.append({t.name for t in tools})
        return self


@pg
def test_chat_task_without_system_default_waits_for_model_choice_then_saves(monkeypatch, tmp_path):
    async def no_tools(*args, **kwargs):
        return []

    monkeypatch.setattr(agent_module, "build_agent_tools", no_tools)
    selected_id = "custom:a-chat"
    changes = {"name": "晚安祝福", "instruction": "每天发一段晚安祝福。",
               "schedule": {"kind": "daily", "local_time": "19:25"}, "model_id": selected_id}
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "configure_task", "args": {"action": "inspect"}, "id": "inspect"}]),
        AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {
            "question": "请选择每天 19:25 执行晚安任务的模型",
            "options": [{"id": "custom:z-system", "label": "z-system"},
                        {"id": selected_id, "label": "a-chat"}],
        }, "id": "choose-model"}]),
        AIMessage(content="", tool_calls=[{"name": "configure_task", "args": {
            "action": "save", "changes": changes, "expected_version": 0,
        }, "id": "save"}]),
        AIMessage(content="已保存，每天北京时间 19:25 发一段晚安祝福。"),
    ])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *a, **k: model)

    async def run():
        async with database_fixture() as storage:
            await default_models(storage)
            await storage.update_model_row("z-system", is_default=False)
            runtime = database_runtime(storage, tmp_path)
            context = await turn(storage, content="创建一个任务，每天晚上19点25给我发一段晚安的祝福。")
            agent = await build_research_agent(
                runtime.settings, model=resolved_model(), workspace_dir=tmp_path / "workspace",
                checkpointer=InMemorySaver(), runtime_backend=StateBackend(),
                client_capabilities=[USER_INPUT_CAPABILITY],
                task_configuration_provider=TaskConfigurationService(runtime),
            )
            config = {"configurable": {"thread_id": context.conversation_id}}
            paused = await agent.ainvoke({"messages": [HumanMessage(
                id=context.user_message_id, content="每天晚上19点25给我发一段晚安的祝福。",
            )]}, config, context=context)
            inspected = json.loads(next(m.content for m in paused["messages"]
                                        if isinstance(m, ToolMessage) and m.name == "configure_task"))
            assert inspected["default_model_id"] is None
            assert selected_id in [m["id"] for m in inspected["available_models"]]
            repository = TaskService(runtime.settings, storage).repository
            assert (await repository.list_tasks("admin"))["items"] == []
            pending, = paused["__interrupt__"]
            request = {**pending.value, "id": pending.id}
            card = serialize_pending_user_question(
                request, interaction_id="model-choice", assistant_message_id="assistant-1",
                expires_at="2030-01-01T00:00:00+00:00",
            )
            assert card["questions"][0]["options"][1]["id"] == selected_id
            command, answer = build_user_input_resume_command(request, {
                "type": "batch", "answers": {"question-1": {"type": "option", "option_id": selected_id}},
            })
            completed = await agent.ainvoke(command, config, context=context)
            assert not completed.get("__interrupt__")
            question_result = next(m for m in completed["messages"]
                                   if isinstance(m, ToolMessage) and m.name == "ask_user")
            assert question_result.status == "success"
            assert json.loads(question_result.content)["answer"] == answer
            tasks = (await repository.list_tasks("admin"))["items"]
            assert len(tasks) == 1
            task = tasks[0]
            assert task["definition"]["model_id"] == selected_id
            assert task["definition"]["schedule"]["kind"] == "daily"
            assert task["definition"]["schedule"]["local_time"] == "19:25"
            assert task["status"] == "enabled" and task["conversation_id"] == UUID(context.conversation_id)
            assert task["latest_run"] is None

    asyncio.run(run())


@pg
def test_actual_chat_agent_calls_tool_and_subagent_cannot_configure(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def no_tools(*args, **kwargs):
        return []
    monkeypatch.setattr(agent_module, "build_agent_tools", no_tools)
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "configure_task", "args": {"action": "inspect"}, "id": "inspect"}]),
        AIMessage(content="", tool_calls=[{"name": "configure_task", "args": {"action": "save", "changes": CREATION, "expected_version": 0}, "id": "save"}]),
        AIMessage(content="任务已创建，每天北京时间9点继续写一集。"),
    ])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *a, **k: model)

    async def run():
        from test_task_agent import resolved_model

        async with database_fixture() as storage:
            runtime = runtime_for(storage, tmp_path)
            context = await turn(storage)
            agent = await build_research_agent(runtime.settings, model=resolved_model(),
                workspace_dir=tmp_path / "workspace", checkpointer=InMemorySaver(),
                runtime_backend=StateBackend(), task_configuration_provider=TaskConfigurationService(runtime))
            config = {"configurable": {"thread_id": context.conversation_id}}
            result = await agent.ainvoke({"messages": [HumanMessage(id=context.user_message_id, content="每天9点写一集，共30集。") ]}, config, context=context)
            results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
            assert len(results) == 2 and all(m.status == "success" for m in results)
            assert json.loads(results[-1].content)["task"]["conversation_id"] == context.conversation_id
            assert "configure_task" in model.bindings[0]
            # 子 Agent 主动尝试同名工具也无法执行实际写入。
            model.responses = [
                AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "尝试配置任务", "subagent_type": "general-purpose"}, "id": "delegate"}]),
                AIMessage(content="", tool_calls=[{"name": "configure_task", "args": {"action": "save", "changes": {"enabled": False}, "expected_version": 1}, "id": "forbidden"}]),
                AIMessage(content="子任务无配置工具。"), AIMessage(content="完成"),
            ]
            model.i = 0
            model.bindings.clear()
            await agent.ainvoke({"messages": [HumanMessage(id="delegate-user", content="请委派一个子任务。") ]}, config, context=context)
            assert any("configure_task" not in names for names in model.bindings)
            task = (await TaskService(runtime.settings, storage).repository.list_tasks("admin"))["items"][0]
            assert task["version"] == 1 and task["status"] == "enabled"
    asyncio.run(run())


def test_tool_schema_hides_runtime_and_rejects_foreign_identifiers():
    tool = build_task_configuration_tool(None)
    schema = tool.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"action", "changes", "expected_version"}
    assert "runtime" not in schema["properties"]
    assert "configure_task" not in INTERPRETER_PTC_TOOLS
    with pytest.raises(ValueError):
        TaskChanges.model_validate({"user_id": "admin", "name": "越权"})
