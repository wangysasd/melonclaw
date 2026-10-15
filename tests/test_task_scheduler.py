"""APScheduler 真实触发、单实例、会话失效、生命周期和重启验收。"""

import asyncio
import os
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text, update
from test_task_agent import Model, settings_for
from test_task_postgres import claim, database_fixture, definition, enqueue, install_model

from melonclaw.core import task_agent
from melonclaw.database.schema import tasks
from melonclaw.repository.task_queue import TaskExecutorUnavailable
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tasks import TaskService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)


async def wait_status(service, run_id, status):
    async with asyncio.timeout(12):
        while True:
            row = await service.get_run("admin", UUID(str(run_id)))
            if row["status"] == status:
                return row
            await asyncio.sleep(0.05)


def test_apscheduler_dispatches_and_executes_without_worker(monkeypatch, tmp_path):
    install_model(monkeypatch)
    monkeypatch.setattr(
        task_agent,
        "build_chat_model",
        lambda *a, **k: Model(responses=[AIMessage(content="定时执行结果")]),
    )

    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(
                settings_for(tmp_path, task_poll_seconds=1), storage, InMemorySaver()
            )
            when = (datetime.now(UTC) + timedelta(hours=1)).replace(second=0, microsecond=0)
            saved = await executor.service.save(
                "admin",
                definition=definition(schedule={"kind": "once", "run_at": when.isoformat()}),
                enabled=True,
                idempotency_key=str(uuid4()),
            )
            async with storage.engine.begin() as conn:
                await conn.execute(
                    update(tasks)
                    .where(tasks.c.id == UUID(saved["task"]["id"]))
                    .values(next_run_at=datetime.now(UTC))
                )
            try:
                await executor.start()
                async with asyncio.timeout(12):
                    while True:
                        history = await executor.service.repository.runs(
                            "admin", UUID(saved["task"]["id"])
                        )
                        if history["items"] and history["items"][0]["status"] == "succeeded":
                            break
                        await asyncio.sleep(0.05)
                assert history["total"] == 1
                assert history["items"][0]["trigger_type"] == "schedule"
                assert executor.status()["ready"] and executor.status()["last_scan_at"]
                async with storage.engine.connect() as conn:
                    assert await conn.scalar(text("SELECT count(*) FROM chat_messages")) == 2
            finally:
                await executor.close()
            assert not executor.status()["ready"] and not executor.jobs

    asyncio.run(run())


def test_second_executor_cannot_reset_live_run_and_lost_connection_cannot_write(
    monkeypatch, tmp_path
):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            await enqueue(service)
            queue = storage.task_queue
            owned = await claim(queue)
            other = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            with pytest.raises(TaskExecutorUnavailable, match="已有任务执行器"):
                await other.start()
            assert (await service.get_run("admin", owned["id"]))["status"] == "running"
            assert await queue.progress(owned, "保留部分结果", max_chars=100)
            # 断开持锁物理连接，后继实例才能获得锁并收敛旧运行。
            await queue.connection.invalidate()
            replacement = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            try:
                await replacement.start()
                with pytest.raises(TaskExecutorUnavailable):
                    await queue.finish(owned, status="succeeded", result="旧执行覆盖")
                row = await service.get_run("admin", owned["id"])
                assert row["status"] == "failed" and row["error_code"] == "service_restarted"
                assert row["result_markdown"] == "保留部分结果"
            finally:
                await replacement.close()

    asyncio.run(run())


def test_executor_stops_jobs_on_database_session_loss(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            started, stopped = asyncio.Event(), asyncio.Event()

            async def hanging(*args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", hanging)
            executor = TaskScheduler(
                settings_for(tmp_path, task_poll_seconds=1), storage, InMemorySaver()
            )
            await enqueue(executor.service)
            try:
                await executor.start()
                await asyncio.wait_for(started.wait(), 5)
                await executor.queue.connection.invalidate()
                async with asyncio.timeout(5):
                    while not executor.stopping:
                        await asyncio.sleep(0.05)
                await asyncio.wait_for(stopped.wait(), 3)
                assert not executor.status()["ready"] and executor.status()["error"]
            finally:
                await executor.close()

    asyncio.run(run())


def test_web_process_lifecycle_runs_cancels_and_recovers_tasks(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            async with storage.engine.connect() as conn:
                schema = await conn.scalar(text("SELECT current_schema()"))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            release = tmp_path / "release"
            env = dict(
                os.environ,
                TASK_TEST_SCHEMA=schema,
                TASK_TEST_WORKDIR=str(tmp_path),
                TASK_TEST_RELEASE=str(release),
                profile="dev",
            )
            children = []

            async def spawn():
                child = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).with_name("task_process_fixture.py")),
                    str(port),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                children.append(child)
                return child

            async def ready(client):
                async with asyncio.timeout(15):
                    while True:
                        try:
                            if (await client.get("/api/ready")).status_code == 200:
                                return
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.1)

            service = TaskService(settings_for(tmp_path), storage)
            try:
                web = await spawn()
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=2
                ) as client:
                    await ready(client)
                    assert (
                        await client.post(
                            "/api/auth/login", json={"user_id": "admin", "password": "admin"}
                        )
                    ).status_code == 200
                    saved = (
                        await client.post(
                            "/api/tasks",
                            json={
                                "definition": definition(),
                                "enabled": True,
                                "idempotency_key": str(uuid4()),
                                "run_now": True,
                            },
                        )
                    ).json()
                    assert await asyncio.wait_for(web.stdout.readline(), 10) == b"EXECUTING\n"
                    release.touch()
                    await wait_status(service, saved["run"]["id"], "succeeded")
                    release.unlink()
                    ongoing = await enqueue(service)
                    assert await asyncio.wait_for(web.stdout.readline(), 10) == b"EXECUTING\n"
                    web.terminate()
                    await asyncio.wait_for(web.wait(), 8)
                    stopped = await service.get_run("admin", UUID(ongoing["run"]["id"]))
                    assert (
                        stopped["status"] == "failed"
                        and stopped["error_code"] == "service_shutdown"
                    )
                    web = await spawn()
                    await ready(client)
                    assert (await client.get("/api/task-runs/" + saved["run"]["id"])).json()[
                        "result_markdown"
                    ] == "Web 内置任务完成"
                    crashed = await enqueue(service)
                    assert await asyncio.wait_for(web.stdout.readline(), 10) == b"EXECUTING\n"
                    web.kill()
                    await web.wait()
                    assert (await service.get_run("admin", UUID(crashed["run"]["id"])))[
                        "status"
                    ] == "running"
                    web = await spawn()
                    await ready(client)
                    recovered = await service.get_run("admin", UUID(crashed["run"]["id"]))
                    assert (
                        recovered["status"] == "failed"
                        and recovered["error_code"] == "service_restarted"
                    )
                    assert (await service.repository.runs("admin", UUID(crashed["task"]["id"])))[
                        "total"
                    ] == 1
                    status = (await client.get("/api/tasks/status")).json()
                    assert status["executor"]["ready"] and "worker" not in status
            finally:
                for child in children:
                    if child.returncode is None:
                        child.terminate()
                for child in children:
                    try:
                        await asyncio.wait_for(child.wait(), 5)
                    except TimeoutError:
                        child.kill()
                        await child.wait()

    asyncio.run(run())


def test_shutdown_converges_job_cancelled_before_coroutine_starts(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            saved = await enqueue(executor.service)
            executor.queue = storage.task_queue
            owned = await claim(executor.queue)
            job = asyncio.create_task(executor.execute(owned))
            executor.jobs.add(job)
            job.add_done_callback(executor.jobs.discard)
            await executor.close()
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            assert result["status"] == "failed" and result["error_code"] == "service_shutdown"
            assert not executor.jobs

    asyncio.run(run())
