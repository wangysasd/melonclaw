"""真实 PostgreSQL 队列/管理/API 验证；仅清理每次生成的临时 schema。"""

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from test_task_agent import Model, resolved_model, settings_for

from melonclaw.api.app import create_app
from melonclaw.core import task_agent
from melonclaw.core.task_errors import TaskError
from melonclaw.database.schema import (
    metadata,
    task_runs,
    tasks,
)
from melonclaw.database.update_runner import apply_schema_updates
from melonclaw.database.updates.task_center import TASK_TABLE_NAMES
from melonclaw.repository import BusinessRepository, seed_demo_data
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.task_queue import TaskQueueRepository
from melonclaw.services.accounts import AccountService
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tasks import TaskService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)


@asynccontextmanager
async def database_fixture():
    url = os.environ["MELONCLAW_TEST_DATABASE_URL"]
    schema = "test_tasks_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    database = SimpleNamespace(engine=engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: metadata.create_all(
                    sync,
                    tables=[t for t in metadata.sorted_tables if t.name not in TASK_TABLE_NAMES],
                )
            )
        await seed_demo_data(database)
        assert "20261010_task_center" in await apply_schema_updates(database)
        assert await apply_schema_updates(database) == ()
        storage = BusinessRepository(database)
        storage.task_queue = TaskQueueRepository(engine)
        yield storage
    finally:
        if "storage" in locals():
            await storage.task_queue.close()
        await engine.dispose()
        async with control.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


def install_model(monkeypatch):
    async def resolve(*args, **kwargs):
        return resolved_model()

    monkeypatch.setattr(TaskService, "resolve_model", resolve)


def definition(**overrides):
    return (
        dict(
            name="测试任务",
            instruction="输出简单文字",
            model_id="custom:offline",
            schedule={"kind": "manual"},
        )
        | overrides
    )


async def enqueue(service, user="admin", *, enabled=True, **overrides):
    return await service.save(
        user,
        definition=definition(**overrides),
        enabled=enabled,
        idempotency_key=str(uuid4()),
        run_now=True,
    )


async def claim(queue, *, global_limit=2):
    if queue.connection is None:
        await queue.open()
    return await queue.claim(global_limit=global_limit, user_limit=1)


def test_atomic_management_queue_and_restart(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            queue = storage.task_queue
            key = str(uuid4())
            args = dict(definition=definition(), enabled=True, idempotency_key=key, run_now=True)
            first, replay = await asyncio.gather(
                service.save("admin", **args), service.save("admin", **args)
            )
            assert first == replay
            tid = UUID(first["task"]["id"])
            with pytest.raises(TaskError, match="不同操作"):
                await service.save("admin", **{**args, "run_now": False})
            with pytest.raises(TaskError, match="已有排队"):
                await service.save(
                    "admin",
                    definition={**definition(), "name": "不应保存"},
                    enabled=True,
                    task_id=tid,
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    run_now=True,
                )
            assert (await service.repository.get_task("admin", tid))["version"] == 1
            claims = await asyncio.gather(claim(queue), claim(queue))
            owned = next(c for c in claims if c)
            assert sum(c is not None for c in claims) == 1
            second = await enqueue(service)
            assert await claim(queue) is None  # 用户并发配额
            assert await queue.progress(owned, "部分文字", max_chars=100)
            with pytest.raises(ValueError):
                await queue.finish(owned, status="succeeded", result="x" * 101, max_chars=100)
            assert (await service.get_run("admin", owned["id"]))["status"] == "running"
            assert await queue.finish(owned, status="succeeded", result="最终文字")
            assert not await queue.finish(owned, status="failed")
            next_run = await claim(queue)
            assert str(next_run["id"]) == second["run"]["id"]
            await queue.close()
            await queue.open()
            assert await queue.interrupt_running("service_restarted") == 1
            assert not await queue.finish(next_run, status="succeeded", result="late")
            assert not await queue.progress(next_run, "late", max_chars=100)
            assert (await service.get_run("admin", next_run["id"]))["error_code"] == "service_restarted"
            third = await enqueue(service)
            async with storage.engine.begin() as conn:
                await conn.execute(
                    update(task_runs)
                    .where(task_runs.c.id == UUID(third["run"]["id"]))
                    .values(queued_at=func.now() - text("INTERVAL '2 days'"))
                )
            assert await queue.expire_queued(86400) == 1
            archived = await enqueue(service)
            await service.archive(
                "admin",
                UUID(archived["task"]["id"]),
                expected_version=1,
                idempotency_key=str(uuid4()),
            )
            assert (await service.get_run("admin", UUID(archived["run"]["id"])))[
                "status"
            ] == "cancelled"
            assert await claim(queue) is None

    asyncio.run(run())


def test_cookie_api_permissions_versions_and_identity_changes(monkeypatch, tmp_path):
    install_model(monkeypatch)
    monkeypatch.setenv("profile", "dev")

    async def run():
        async with database_fixture() as storage:
            settings = settings_for(tmp_path)
            accounts = AccountService(storage)
            await accounts.save_tenant("team", "测试租户", True, create=True)
            await accounts.save_user("alice", "用户甲", "team", password="alice-pass")
            app = create_app()
            app.state.chat = SimpleNamespace(storage=storage, settings=settings, ready=True)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                assert (await client.get("/api/tasks")).status_code == 401
                assert (
                    await client.post(
                        "/api/auth/login", json={"user_id": "alice", "password": "alice-pass"}
                    )
                ).status_code == 200
                assert (await client.get("/api/task-notifications")).status_code == 404
                assert (await client.post(f"/api/task-notifications/{uuid4()}/read")).status_code == 404
                body = {
                    "definition": definition(),
                    "enabled": True,
                    "idempotency_key": str(uuid4()),
                    "run_now": True,
                }
                response = await client.post("/api/tasks", json=body)
                assert response.status_code == 201, response.text
                saved = response.json()
                tid, rid = saved["task"]["id"], saved["run"]["id"]
                assert (await client.post("/api/task-chat/context", json={"run_id": rid})).status_code == 404
                assert (await client.post("/api/tasks", json=body)).json() == saved
                assert (await client.get("/api/tasks?page=0")).status_code == 422
                assert (await client.get("/api/tasks/status")).json()["executor"]["ready"] is False
                assert (
                    await client.patch(
                        f"/api/tasks/{tid}",
                        json={**body, "idempotency_key": str(uuid4()), "expected_version": 99},
                    )
                ).status_code == 409
                assert (
                    await client.post("/api/tasks", json={**body, "owner_user_id": "admin"})
                ).status_code == 422
                with pytest.raises(AccountConflict):
                    await accounts.save_user("alice", "用户甲", "system")
                assert (
                    await client.post("/api/auth/switch", json={"user_id": "admin"})
                ).status_code == 200
                assert (await client.get(f"/api/tasks/{tid}")).status_code == 404
                assert (await client.get(f"/api/task-runs/{rid}")).status_code == 404
                assert (await client.get(f"/api/task-runs/{rid}/events")).status_code == 404
                assert (
                    await client.post(
                        f"/api/task-runs/{rid}/cancel", json={"idempotency_key": str(uuid4())}
                    )
                ).status_code == 404
                assert (
                    await client.post(
                        f"/api/task-runs/{rid}/retry",
                        json={"idempotency_key": str(uuid4()), "expected_version": 1},
                    )
                ).status_code == 404
                assert (await client.get("/api/tasks")).json()["total"] == 0
                assert (await client.get(f"/api/tasks/{tid}/runs")).status_code == 404
                assert (
                    await client.patch(f"/api/tasks/{tid}", json={**body, "expected_version": 1})
                ).status_code == 404
                for action in ("runs", "archive", "enabled"):
                    payload = {"idempotency_key": str(uuid4()), "expected_version": 1}
                    if action == "enabled":
                        payload["enabled"] = False
                    assert (
                        await client.post(f"/api/tasks/{tid}/{action}", json=payload)
                    ).status_code == 404
                service = TaskService(settings, storage)
                await service.archive(
                    "alice", UUID(tid), expected_version=1, idempotency_key=str(uuid4())
                )
                inactive = await service.save(
                    "alice", definition=definition(), enabled=True, idempotency_key=str(uuid4())
                )
                await accounts.save_user("alice", "用户甲", "system")
                assert (await service.repository.list_tasks("alice"))["total"] == 0
                async with storage.engine.connect() as conn:
                    row = (
                        (
                            await conn.execute(
                                select(tasks).where(tasks.c.id == UUID(inactive["task"]["id"]))
                            )
                        )
                        .mappings()
                        .one()
                    )
                    assert row["status"] == "paused" and row["pause_reason"] == "tenant_changed"

    asyncio.run(run())


def test_executor_delivers_real_graph_result_to_task_chat(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO", logger="melonclaw.services.task_scheduler")
    install_model(monkeypatch)
    monkeypatch.setattr(
        task_agent,
        "build_chat_model",
        lambda *a, **k: Model(responses=[AIMessage(content="任务执行成功")]),
    )

    async def run():
        async with database_fixture() as storage:
            settings = settings_for(
                tmp_path, task_poll_seconds=1
            )
            executor = TaskScheduler(settings, storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            owned = await claim(executor.queue)
            await executor.execute(owned)
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            assert result["status"] == "succeeded", result
            assert result["result_markdown"] == "任务执行成功"
            assert result["result_is_partial"] is False
            assert "run_id=" + saved["run"]["id"] in caplog.text
            assert "status=running" in caplog.text and "status=succeeded" in caplog.text
            assert "任务执行成功" not in caplog.text
            async with storage.engine.connect() as conn:
                assert await conn.scalar(text("SELECT count(*) FROM chat_messages")) == 2
                assert await conn.scalar(text("SELECT count(*) FROM chat_conversations")) == 1

    asyncio.run(run())


def test_history_quotas_project_independence_and_shutdown(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            accounts = AccountService(storage)
            await accounts.save_user("bob", "用户乙", "system", password="bob-pass")
            settings = settings_for(
                tmp_path, task_poll_seconds=1
            )
            executor = TaskScheduler(settings, storage, InMemorySaver())
            executor.queue = storage.task_queue
            service, queue = executor.service, executor.queue
            project = await storage.create_project("admin", "任务项目")
            saved = await enqueue(service)
            tid = UUID(saved["task"]["id"])
            await storage.update_project(UUID(project["id"]), "admin", delete=True)
            edited = await service.save(
                "admin",
                task_id=tid,
                expected_version=1,
                definition={**definition(), "instruction": "修改后的要求"},
                enabled=True,
                idempotency_key=str(uuid4()),
            )
            assert edited["task"]["version"] == 2
            snapshot = (await service.get_run("admin", UUID(saved["run"]["id"])))["config_snapshot"]
            assert (
                snapshot["task_version"] == 1
                and snapshot["definition"]["instruction"] == "输出简单文字"
            )
            bob = await enqueue(service, "bob")
            first = await claim(queue, global_limit=1)
            assert await claim(queue, global_limit=1) is None
            assert await queue.finish(first, status="succeeded", result="完成")
            second = await claim(queue, global_limit=1)
            assert str(second["id"]) == bob["run"]["id"]
            started, stopped = asyncio.Event(), asyncio.Event()

            async def hanging_agent(*args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            monkeypatch.setattr(
                "melonclaw.services.task_scheduler.build_research_agent", hanging_agent
            )
            job = asyncio.create_task(executor.execute(second))
            await asyncio.wait_for(started.wait(), 2)
            job.cancel()
            await job
            assert stopped.is_set()
            assert (await service.get_run("bob", second["id"]))["error_code"] == "service_shutdown"

    asyncio.run(run())


def test_real_model_resolution_reuses_single_transaction_connection(tmp_path):
    """一个连接也能并发保存，防止全局锁等待者耗尽连接后相互等待。"""

    async def run():
        async with database_fixture() as setup:
            await setup.create_provider_row(
                provider_key="offline",
                scope="global",
                source_type="manual",
                display_name="离线配置",
                provider_type="openai_compatible",
                base_url="https://example.invalid/v1",
                api_key="offline-placeholder",
                models_endpoint=None,
                created_by="admin",
            )
            await setup.create_model_row(
                model_key="offline",
                provider_key="offline",
                scope="global",
                source_type="manual",
                display_name="离线配置",
                model_name="offline",
                created_by="admin",
            )
            async with setup.engine.connect() as conn:
                schema = await conn.scalar(text("SELECT current_schema()"))
            engine = create_async_engine(
                os.environ["MELONCLAW_TEST_DATABASE_URL"],
                pool_size=1,
                max_overflow=0,
                pool_timeout=0.5,
                connect_args={"server_settings": {"search_path": schema}},
            )
            try:
                service = TaskService(
                    settings_for(tmp_path), BusinessRepository(SimpleNamespace(engine=engine))
                )
                saved = await asyncio.wait_for(
                    asyncio.gather(
                        *[
                            service.save(
                                "admin",
                                definition=definition(),
                                enabled=True,
                                idempotency_key=str(uuid4()),
                            )
                            for _ in range(3)
                        ]
                    ),
                    5,
                )
                assert len({value["task"]["id"] for value in saved}) == 3
            finally:
                await engine.dispose()

    asyncio.run(run())


def test_task_list_status_priority_before_pagination(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            ids = {}
            # Archived is newest, but must still be on the last page.
            for status in ("enabled", "paused", "archived"):
                saved = await service.save(
                    "admin", definition=definition(name=status), enabled=True,
                    idempotency_key=str(uuid4()), run_now=False,
                )
                ids[status] = UUID(saved["task"]["id"])
                async with storage.task_queue.engine.begin() as conn:
                    await conn.execute(update(tasks).where(tasks.c.id == ids[status]).values(status=status))
            repository = service.repository
            for page, status in enumerate(("enabled", "paused", "archived"), start=1):
                result = await repository.list_tasks("admin", page=page, page_size=1)
                assert result["total"] == 3
                assert [row["id"] for row in result["items"]] == [ids[status]]
            filtered = await repository.list_tasks("admin", status="paused")
            assert filtered["total"] == 1
            assert filtered["items"][0]["id"] == ids["paused"]

    asyncio.run(run())
