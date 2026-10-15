"""真实 PostgreSQL 调度、取消、当前配置重跑和精确清理；可控时间不实际等待多天。"""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, text, update
from test_task_agent import resolved_model, settings_for
from test_task_postgres import claim, database_fixture, definition, enqueue, install_model

from melonclaw.core.task_errors import TaskError
from melonclaw.database.schema import task_runs
from melonclaw.repository.tasks import TaskTransaction
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tasks import TaskService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)


async def save_plan(service, schedule, *, enabled=True):
    return await service.save(
        "admin",
        definition={**definition(), "schedule": schedule},
        enabled=enabled,
        idempotency_key=str(uuid4()),
    )


async def task_runs_for(storage, task_id):
    async with storage.engine.connect() as conn:
        return (
            (
                await conn.execute(
                    select(task_runs)
                    .where(task_runs.c.task_id == UUID(str(task_id)))
                    .order_by(task_runs.c.created_at, task_runs.c.id)
                )
            )
            .mappings()
            .all()
        )


def test_two_schedulers_three_cycles_overlap_and_missed(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            first = TaskService(settings_for(tmp_path), storage)
            second = TaskService(settings_for(tmp_path), storage)
            queue = storage.task_queue
            saved = await save_plan(first, {"kind": "daily", "local_time": "09:00"})
            tid = saved["task"]["id"]
            start = datetime.fromisoformat(saved["task"]["next_run_at"])
            for day in range(3):
                at = start + timedelta(days=day)
                assert (
                    sum(await asyncio.gather(first.dispatch_due(at=at), second.dispatch_due(at=at)))
                    == 1
                )
                owned = await claim(queue)
                assert owned["scheduled_for"] == at
                assert await queue.finish(owned, status="succeeded", result=f"周期 {day}")
            rows = await task_runs_for(storage, tid)
            assert len(rows) == 3 and len({r["execution_context_id"] for r in rows}) == 3
            # 手动运行保持 next_run_at；计划重叠直接跳过。
            current = await first.repository.get_task("admin", UUID(tid))
            await first.run(
                "admin",
                UUID(tid),
                expected_version=current["version"],
                idempotency_key=str(uuid4()),
            )
            await first.dispatch_due(at=start + timedelta(days=3))
            rows = await task_runs_for(storage, tid)
            assert len(rows) == 4 and rows[-1]["status"] == "queued"
            assert (await first.repository.get_task("admin", UUID(tid)))["has_active_run"] is True
            manual = next(r for r in rows if r["status"] == "queued")
            await first.cancel("admin", manual["id"], idempotency_key=str(uuid4()))
            # 停机错过的周期不补跑，也不创建运行。
            await first.dispatch_due(at=start + timedelta(days=7, seconds=301))
            rows = await task_runs_for(storage, tid)
            assert len(rows) == 4
            assert (await first.repository.get_task("admin", UUID(tid)))[
                "next_run_at"
            ] == start + timedelta(days=8)
            async with storage.engine.connect() as conn:
                assert await conn.scalar(text("SELECT count(*) FROM chat_messages")) == 5

    asyncio.run(run())


def test_pause_resume_expired_once_archive_and_atomic_dispatch(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            when = (datetime.now(UTC) + timedelta(hours=1)).replace(second=0, microsecond=0)
            saved = await save_plan(
                service, {"kind": "once", "run_at": when.isoformat()}, enabled=False
            )
            tid = UUID(saved["task"]["id"])

            async def clock(self):
                return when + timedelta(seconds=1)

            monkeypatch.setattr(TaskTransaction, "current_time", clock)
            resumed = await service.set_enabled(
                "admin", tid, enabled=True, expected_version=1, idempotency_key=str(uuid4())
            )
            assert resumed["next_run_at"] is None
            rows = await task_runs_for(storage, tid)
            assert rows == []
            assert await service.dispatch_due(at=when + timedelta(seconds=2)) == 0
            # 修改其他字段不能把已触发的一次计划重新安排。
            definition2 = {**resumed["definition"], "instruction": "编辑要求"}
            edited = await service.save(
                "admin",
                task_id=tid,
                definition=definition2,
                enabled=True,
                expected_version=2,
                idempotency_key=str(uuid4()),
            )
            assert edited["task"]["next_run_at"] is None
            await service.archive("admin", tid, expected_version=3, idempotency_key=str(uuid4()))
            with pytest.raises(TaskError, match="归档"):
                await service.set_enabled(
                    "admin", tid, enabled=True, expected_version=4, idempotency_key=str(uuid4())
                )
            daily = await save_plan(service, {"kind": "daily", "local_time": "09:00"})
            did = UUID(daily["task"]["id"])
            at = datetime.fromisoformat(daily["task"]["next_run_at"])
            original = TaskTransaction.advance_schedule

            async def failing(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("transaction failure")

            monkeypatch.setattr(TaskTransaction, "advance_schedule", failing)
            with pytest.raises(RuntimeError):
                await service.dispatch_due(at=at)
            assert not await task_runs_for(storage, did)
            assert (await service.repository.get_task("admin", did))["next_run_at"] == at
            monkeypatch.setattr(TaskTransaction, "advance_schedule", original)
            await service.set_enabled(
                "admin", did, enabled=False, expected_version=1, idempotency_key=str(uuid4())
            )
            assert await service.dispatch_due(at=at + timedelta(days=3)) == 0
            # 恢复计算未来周期，不沿用暂停前的到期游标。
            resumed_daily = await service.set_enabled(
                "admin", did, enabled=True, expected_version=2, idempotency_key=str(uuid4())
            )
            assert datetime.fromisoformat(resumed_daily["next_run_at"]) > datetime.now(UTC)

    asyncio.run(run())


def test_cancel_ack_completion_race_and_current_configuration_rerun(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            settings = settings_for(
                tmp_path, task_poll_seconds=1
            )
            executor = TaskScheduler(settings, storage, InMemorySaver())
            executor.queue = storage.task_queue
            service, queue = executor.service, executor.queue
            saved = await enqueue(service)
            tid, rid = UUID(saved["task"]["id"]), UUID(saved["run"]["id"])
            owned = await claim(queue)
            entered, stopped = asyncio.Event(), asyncio.Event()

            async def hanging(*args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", hanging)
            executing = asyncio.create_task(executor.execute(owned))
            await asyncio.wait_for(entered.wait(), 2)
            request = await service.cancel("admin", rid, idempotency_key=str(uuid4()))
            assert request["status"] == "running" and request["cancel_requested_at"]
            await asyncio.wait_for(executing, 3)
            assert (
                stopped.is_set() and (await service.get_run("admin", rid))["status"] == "cancelled"
            )
            # 最终成功先提交，迟到取消不能重写。
            next_run = await service.run(
                "admin", tid, expected_version=1, idempotency_key=str(uuid4())
            )
            owned = await claim(queue)
            await queue.finish(owned, status="succeeded", result="完整结果")
            assert (
                await service.cancel("admin", UUID(next_run["id"]), idempotency_key=str(uuid4()))
            )["status"] == "succeeded"
            failed = await service.run(
                "admin", tid, expected_version=1, idempotency_key=str(uuid4())
            )
            owned = await claim(queue)
            await queue.finish(owned, status="failed", result="部分结果", error_code="injected")
            await service.save(
                "admin",
                task_id=tid,
                expected_version=1,
                definition={**definition(), "instruction": "新的任务要求"},
                enabled=True,
                idempotency_key=str(uuid4()),
            )
            rerun = await service.run(
                "admin", tid, expected_version=2, idempotency_key=str(uuid4())
            )
            assert rerun["config_snapshot"]["definition"]["instruction"] == "新的任务要求"
            assert rerun["config_snapshot"]["task_version"] == 2
            assert (
                rerun["id"] != failed["id"]
                and rerun["execution_context_id"] != failed["execution_context_id"]
            )

            async def changed(*args, **kwargs):
                return replace(resolved_model(), config_version=2)

            monkeypatch.setattr(TaskService, "resolve_model", changed)
            # 模型版本变化只影响实际执行，历史配置不充当重试授权。
            _, _, model = await service.execution_input(
                await service.repository.get_run("admin", UUID(rerun["id"]))
            )
            assert model.config_version == 2

    asyncio.run(run())


def test_scheduled_model_failure_finishes_and_notifies(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            settings = settings_for(
                tmp_path, task_poll_seconds=1
            )
            executor = TaskScheduler(settings, storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await save_plan(executor.service, {"kind": "daily", "local_time": "09:00"})
            at = datetime.fromisoformat(saved["task"]["next_run_at"])
            await executor.service.dispatch_due(at=at)

            async def unavailable(*args, **kwargs):
                raise ValueError("model unavailable")

            monkeypatch.setattr(TaskService, "resolve_model", unavailable)
            await executor.execute(await claim(executor.queue))
            rows = await task_runs_for(storage, saved["task"]["id"])
            assert len(rows) == 1 and rows[0]["error_code"] == "model_unavailable"
            assert (await executor.service.repository.get_task("admin", UUID(saved["task"]["id"])))[
                "next_run_at"
            ] > at

    asyncio.run(run())



def test_checkpoint_cleanup_scope_retention_and_upgrade(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        from langgraph.checkpoint.base import empty_checkpoint
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        from melonclaw.database.connection import derive_psycopg_database_url

        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            queue = storage.task_queue
            old, recent, active, wrong_namespace = [await enqueue(service) for _ in range(4)]
            for _ in range(2):
                owned = await claim(queue)
                await queue.finish(owned, status="succeeded", result="保留的结果")
            async with storage.engine.begin() as conn:
                schema = await conn.scalar(text("select current_schema()"))
                await conn.execute(
                    update(task_runs)
                    .where(task_runs.c.id == UUID(old["run"]["id"]))
                    .values(finished_at=func.now() - text("INTERVAL '31 days'"))
                )
                await conn.execute(
                    update(task_runs)
                    .where(task_runs.c.id == UUID(wrong_namespace["run"]["id"]))
                    .values(
                        status="failed",
                        finished_at=func.now() - text("INTERVAL '31 days'"),
                        execution_context_id="chat:keep",
                    )
                )
            pool = AsyncConnectionPool(
                derive_psycopg_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
                open=False,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                    "options": f"-c search_path={schema}",
                },
            )
            await pool.open(wait=True)
            try:
                saver = AsyncPostgresSaver(pool)
                await saver.setup()
                threads = [
                    item["run"]["execution_context_id"] for item in (old, recent, active)
                ] + ["chat:keep"]
                for thread in threads:
                    await saver.aput(
                        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
                        empty_checkpoint(),
                        {"source": "input", "step": 0, "parents": {}},
                        {},
                    )
                assert await service.cleanup_checkpoints(saver) == 1
                assert await service.cleanup_checkpoints(saver) == 0
                assert await saver.aget_tuple({"configurable": {"thread_id": threads[0]}}) is None
                for thread in threads[1:]:
                    assert (
                        await saver.aget_tuple({"configurable": {"thread_id": thread}}) is not None
                    )
                result = await service.get_run("admin", UUID(old["run"]["id"]))
                assert result["result_markdown"] == "保留的结果" and result["checkpoint_cleaned_at"]
            finally:
                await pool.close()

    asyncio.run(run())
