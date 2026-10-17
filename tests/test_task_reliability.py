"""任务摘要边界、运行准备时限、只读锁和保留数据升级回归。"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, inspect, select, text, update
from test_task_agent import Model, settings_for
from test_task_postgres import claim, database_fixture, definition, enqueue, install_model

from melonclaw.core import task_agent
from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_policy import TaskExecutionError, TaskRunControl
from melonclaw.core.task_snapshot import TaskDefinition
from melonclaw.database.schema import chat_messages, task_operations, tasks
from melonclaw.database.update_runner import apply_schema_updates
from melonclaw.repository.task_queue import TaskExecutorUnavailable
from melonclaw.repository.tasks import task_lock
from melonclaw.services.task_execution import TaskOutcome
from melonclaw.services.task_history import prepare_history, summary_chunks
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tasks import TaskService

pg = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL")


def history_rows():
    return [{"id": uuid4(), "seq": i + 1, "role": "assistant", "content": content,
             "status": "completed", "display_metadata": {}, "created_at": datetime.now(UTC)}
            for i, content in enumerate(["旧" * 1000, "新" * 700])]


def snapshot():
    return {"sources": [], "completed_runs": 2, "summary": "", "summary_until": 0}


class SummaryModel:
    def __init__(self, *, hang=False):
        self.started = asyncio.Event()
        self.calls = 0
        self.hang = hang
        self.stopped = False

    def with_config(self, **kwargs):
        return self

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        self.started.set()
        try:
            if self.hang:
                await asyncio.Future()
            return AIMessage(content="旧历史摘要")
        finally:
            self.stopped = True


def test_utf8_chunks_preserve_exact_text_and_preflight_the_call_limit():
    original = "中🙂文abc\n" * 200
    chunks = summary_chunks(original, 128, 40)
    assert "".join(chunks) == original
    assert all(len(chunk.encode()) <= 128 for chunk in chunks)
    with pytest.raises(TaskError, match="调用预算"):
        summary_chunks(original, 128, 1)


def test_summary_limit_fails_before_any_paid_call(monkeypatch, tmp_path):
    model = SummaryModel()
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: model)
    settings = settings_for(tmp_path, task_history_summary_max_calls=1, agent_output_reserve=512, agent_summary_keep_tokens=512)
    with pytest.raises(TaskError) as caught:
        asyncio.run(prepare_history(snapshot(), history_rows(), SimpleNamespace(context_window=8192), settings))
    assert caught.value.error_code == "history_summary_limit"
    assert model.calls == 0


def test_summary_timeout_and_cancellation_stop_inflight_call(monkeypatch, tmp_path):
    async def run():
        for cancel in [False, True]:
            model = SummaryModel(hang=True)
            monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: model)
            settings = settings_for(tmp_path, task_history_timeout_seconds=1, agent_output_reserve=512, agent_summary_keep_tokens=512)
            job = asyncio.create_task(prepare_history(snapshot(), history_rows(), SimpleNamespace(context_window=8192), settings))
            await asyncio.wait_for(model.started.wait(), 2)
            if cancel:
                job.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await job
            else:
                with pytest.raises(TaskError) as caught:
                    await job
                assert caught.value.error_code == "history_summary_timeout"
            assert model.calls == 1 and model.stopped
    asyncio.run(run())


def test_summary_consumes_shared_background_budget(monkeypatch, tmp_path):
    model = SummaryModel()
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: model)
    with pytest.raises(TaskExecutionError) as caught:
        asyncio.run(prepare_history(snapshot(), history_rows(), SimpleNamespace(context_window=8192),
                                    settings_for(tmp_path, agent_output_reserve=512, agent_summary_keep_tokens=512), control=TaskRunControl(2, 1)))
    assert caught.value.code == "model_limit" and model.calls == 2


@pg
def test_status_read_does_not_wait_for_global_task_lock_and_checks_connection(monkeypatch, tmp_path):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            await enqueue(service)
            queue = storage.task_queue
            owned = await claim(queue)
            async with storage.engine.begin() as conn:
                await task_lock(conn)
                assert (await asyncio.wait_for(queue.execution_status(owned), 1))["running"]
            pid = queue.backend_pid
            queue.backend_pid = pid + 1
            with pytest.raises(TaskExecutorUnavailable):
                await queue.execution_status(owned)
            queue.backend_pid = pid
            await queue.connection.invalidate()
            with pytest.raises(TaskExecutorUnavailable):
                await queue.execution_status(owned)
    asyncio.run(run())


@pg
@pytest.mark.parametrize("stage", ["build", "execute"])
def test_background_total_timeout_includes_agent_build(monkeypatch, tmp_path, stage):
    install_model(monkeypatch)
    stopped = asyncio.Event()
    class HangingAgent:
        async def astream(self, *args, **kwargs):
            try:
                await asyncio.Future()
                yield None
            finally:
                stopped.set()
    async def build(*args, **kwargs):
        if stage == "execute":
            return HangingAgent()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", build)
    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path, task_execution_timeout_seconds=1), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            await asyncio.wait_for(executor.execute(await claim(executor.queue)), 3)
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            assert result["status"] == "failed" and result["error_code"] == "execution_timeout"
            assert stopped.is_set()
    asyncio.run(run())


@pg
def test_progress_only_saves_changed_text_and_still_checks_cancellation(monkeypatch, tmp_path):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path, task_poll_seconds=1), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            owned = await claim(executor.queue)
            checks, texts = [], []
            unchanged_checked = asyncio.Event()
            original_status, original_progress = executor.queue.execution_status, executor.queue.progress
            async def status(run):
                result = await original_status(run)
                checks.append(result)
                if len(checks) >= 3:
                    unchanged_checked.set()
                return result
            async def progress(run, value, **kwargs):
                texts.append(value)
                return await original_progress(run, value, **kwargs)
            async def agent(*args, **kwargs):
                return object()
            async def execute(*args, progress, **kwargs):
                progress.text = "保持不变的部分结果"
                await asyncio.Future()
                return TaskOutcome("succeeded", progress.text, False)
            monkeypatch.setattr(executor.queue, "execution_status", status)
            monkeypatch.setattr(executor.queue, "progress", progress)
            monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", agent)
            monkeypatch.setattr("melonclaw.services.task_scheduler.execute_task_agent", execute)
            job = asyncio.create_task(executor.execute(owned))
            await asyncio.wait_for(unchanged_checked.wait(), 5)
            await executor.service.cancel("admin", owned["id"], idempotency_key=str(uuid4()))
            await asyncio.wait_for(job, 3)
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            assert texts == ["保持不变的部分结果"]
            assert result["status"] == "cancelled" and result["result_markdown"] == texts[0]
    asyncio.run(run())


@pg
def test_scheduled_run_syncs_resolved_model_name_to_snapshot_and_chat(monkeypatch, tmp_path):
    install_model(monkeypatch)
    monkeypatch.setattr(task_agent, "build_chat_model", lambda *a, **k: Model(responses=[AIMessage(content="自动结果")]))
    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await executor.service.save("admin", definition=definition(schedule={"kind": "daily", "local_time": "09:00"}), enabled=True, idempotency_key=str(uuid4()))
            assert await executor.service.dispatch_due(at=datetime.fromisoformat(saved["task"]["next_run_at"])) == 1
            owned = await claim(executor.queue)
            await executor.execute(owned)
            result = await executor.service.get_run("admin", owned["id"])
            assert result["status"] == "succeeded"
            assert result["config_snapshot"]["model_display_name"] == "离线模型"
            async with storage.engine.connect() as conn:
                assert await conn.scalar(select(chat_messages.c.model_display_name).where(chat_messages.c.role == "assistant")) == "离线模型"
    asyncio.run(run())


@pg
@pytest.mark.parametrize("initial_title", ["新会话", "用户自己的标题"])
def test_bound_chat_title_tracks_task_name_only_when_automatic(monkeypatch, tmp_path, initial_title):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            conversation = await storage.create_conversation("admin", None)
            cid = UUID(conversation["id"])
            await storage.update_conversation(cid, "admin", title=initial_title)
            async with service.repository.operation("admin", str(uuid4()), {"test": "bind"}) as tx:
                tx.result = await service.save_definition(tx, TaskDefinition.model_validate(definition()), enabled=True, conversation_id=cid, record_setup=False)
                tid = UUID(tx.result["task"]["id"])
            title = (await storage.get_conversation(cid, "admin"))["title"]
            assert title == ("任务：测试任务" if initial_title == "新会话" else initial_title)
            await service.save("admin", task_id=tid, expected_version=1, definition=definition(name="新任务名"), enabled=True, idempotency_key=str(uuid4()))
            assert (await storage.get_conversation(cid, "admin"))["title"] == ("任务：新任务名" if initial_title == "新会话" else initial_title)
            await storage.update_conversation(cid, "admin", title="后来手动改名")
            await service.save("admin", task_id=tid, expected_version=2, definition=definition(name="再次改名"), enabled=True, idempotency_key=str(uuid4()))
            assert (await storage.get_conversation(cid, "admin"))["title"] == "后来手动改名"
    asyncio.run(run())


@pg
def test_expired_operations_keep_rejection_keys_and_never_repeat_creation(monkeypatch, tmp_path):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            key = str(uuid4())
            args = dict(definition=definition(), enabled=True, idempotency_key=key)
            saved = await service.save("admin", **args)
            assert await service.save("admin", **args) == saved
            await service.save("admin", **{**args, "idempotency_key": str(uuid4())})
            async with storage.engine.begin() as conn:
                await conn.execute(update(task_operations).where(task_operations.c.idempotency_key == key).values(created_at=datetime.now(UTC) - timedelta(days=31)))
            with pytest.raises(TaskError) as caught:
                await service.save("admin", **args)
            assert caught.value.error_code == "idempotency_expired"
            assert await service.repository.retire_operations(limit=1) == 1
            assert await service.repository.retire_operations() == 0
            service.repository.operation_retention_days = 60
            with pytest.raises(TaskError) as caught:
                await service.save("admin", **args)
            assert caught.value.error_code == "idempotency_expired"
            async with storage.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(tasks)) == 2
                rows = (await conn.execute(select(task_operations))).mappings().all()
                assert len(rows) == 2
                assert next(r["response"] for r in rows if r["idempotency_key"] == key) is None
                assert next(r["response"] for r in rows if r["idempotency_key"] != key) is not None
    asyncio.run(run())


@pg
def test_reliability_schema_upgrade_preserves_data_and_is_repeatable(monkeypatch, tmp_path):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            saved = await enqueue(service)
            async with storage.engine.begin() as conn:
                await conn.execute(text("ALTER TABLE task_runs ADD COLUMN notification_read_at TIMESTAMPTZ"))
                await conn.execute(text("CREATE INDEX ix_task_runs_notifications ON task_runs (owner_user_id, tenant_id, finished_at)"))
                await conn.execute(text("ALTER TABLE task_operations ALTER COLUMN response SET NOT NULL"))
                await conn.execute(text("DROP INDEX ix_task_operations_retention"))
                await conn.execute(text("DELETE FROM melonclaw_schema_updates WHERE update_id = '20261015_task_reliability'"))
            database = SimpleNamespace(engine=storage.engine)
            assert await apply_schema_updates(database) == ("20261015_task_reliability",)
            assert await apply_schema_updates(database) == ()
            async with storage.engine.connect() as conn:
                columns, indexes, operation_columns = await conn.run_sync(lambda sync: (
                    inspect(sync).get_columns("task_runs"), inspect(sync).get_indexes("task_operations"), inspect(sync).get_columns("task_operations")))
                assert "notification_read_at" not in {c["name"] for c in columns}
                assert "ix_task_operations_retention" in {i["name"] for i in indexes}
                assert next(c for c in operation_columns if c["name"] == "response")["nullable"]
                assert await conn.scalar(select(func.count()).select_from(chat_messages)) == 2
            assert (await service.get_run("admin", UUID(saved["run"]["id"])))["status"] == "queued"
            assert (await service.repository.get_task("admin", UUID(saved["task"]["id"])))["conversation_id"] == UUID(saved["task"]["conversation_id"])
    asyncio.run(run())
