"""单实例执行连接、任务领取、公开进度和终态；不使用逐任务租约。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import func, select, text, update

from melonclaw.database.schema import task_runs, tasks, tenants, users
from melonclaw.repository.task_delivery import sync_run
from melonclaw.repository.tasks import TaskRepository, task_lock


class TaskExecutorUnavailable(RuntimeError):
    """单实例执行器未持有有效数据库会话。"""


class TaskQueueRepository(TaskRepository):
    def __init__(self, engine):
        super().__init__(engine)
        self.connection = None
        self.backend_pid = None
        self.mutex = asyncio.Lock()

    async def open(self):
        async with self.mutex:
            if self.connection is not None:
                return
            connection = await self.engine.connect()
            try:
                async with connection.begin():
                    acquired = await connection.scalar(
                        text("SELECT pg_try_advisory_lock(hashtext(current_schema()), 741925)")
                    )
                    if not acquired:
                        raise TaskExecutorUnavailable(
                            "已有任务执行器运行；只支持单实例 Web，请先停止旧实例。"
                        )
                    self.backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
                self.connection = connection
            except BaseException:
                await connection.invalidate()
                await connection.close()
                raise

    async def close(self):
        async with self.mutex:
            connection, self.connection = self.connection, None
            if connection is not None:
                # 会话关闭会自动释放锁。始终断开物理连接，不把持锁连接归还池。
                try:
                    await connection.invalidate()
                finally:
                    await connection.close()

    @asynccontextmanager
    async def transaction(self, *, task_exclusive=True):
        async with self.mutex:
            connection = self.connection
            if connection is None or connection.closed or connection.invalidated:
                raise TaskExecutorUnavailable("任务执行连接已失效，请重启 Web。")
            async with connection.begin():
                # SQLAlchemy 断线后的重连不能继承会话锁；禁止新连接接着提交旧执行结果。
                if await connection.scalar(text("SELECT pg_backend_pid()")) != self.backend_pid:
                    raise TaskExecutorUnavailable("任务执行连接已变化，请重启 Web。")
                if task_exclusive:
                    await task_lock(connection)
                yield connection

    async def save_model_display_name(self, run, display_name):
        async with self.transaction() as conn:
            snapshot = dict(run["config_snapshot"]) | {"model_display_name": display_name}
            result = await conn.execute(update(task_runs).where(
                task_runs.c.id == run["id"], task_runs.c.status == "running",
            ).values(config_snapshot=snapshot).returning(task_runs))
            row = result.mappings().first()
            if row is None:
                raise TaskExecutorUnavailable("本次任务已结束，不能继续执行。")
            await sync_run(conn, dict(row))
            run["config_snapshot"] = snapshot

    async def claim(self, *, global_limit, user_limit):
        async with self.transaction() as conn:
            running = await conn.scalar(
                select(func.count()).select_from(task_runs).where(task_runs.c.status == "running")
            )
            if running >= global_limit:
                return None
            active = task_runs.alias("active_runs")
            user_running = (
                select(func.count())
                .select_from(active)
                .where(
                    active.c.owner_user_id == task_runs.c.owner_user_id,
                    active.c.status == "running",
                )
                .correlate(task_runs)
                .scalar_subquery()
            )
            query = (
                select(task_runs)
                .join(tasks, task_runs.c.task_id == tasks.c.id)
                .join(users, users.c.user_id == task_runs.c.owner_user_id)
                .join(tenants, tenants.c.tenant_id == task_runs.c.tenant_id)
                .where(
                    task_runs.c.status == "queued",
                    tasks.c.status != "archived",
                    users.c.tenant_id == task_runs.c.tenant_id,
                    users.c.tenant_status == "active",
                    tenants.c.enabled.is_(True),
                    user_running < user_limit,
                )
                .order_by(task_runs.c.queued_at, task_runs.c.id)
                .limit(1)
                .with_for_update(of=task_runs)
            )
            row = (await conn.execute(query)).mappings().first()
            if row is None:
                return None
            result = await conn.execute(
                update(task_runs)
                .where(task_runs.c.id == row["id"])
                .values(status="running", started_at=func.clock_timestamp())
                .returning(task_runs)
            )
            run = dict(result.mappings().one())
            await sync_run(conn, run)
            return run

    async def save_history(self, run, snapshot):
        async with self.transaction() as conn:
            values = dict(run["config_snapshot"]) | {"history": snapshot}
            result = await conn.execute(update(task_runs).where(task_runs.c.id == run["id"], task_runs.c.status == "running").values(config_snapshot=values).returning(task_runs.c.id))
            if result.first() is None:
                raise TaskExecutorUnavailable("本次任务已结束，不能继续执行。")
            run["config_snapshot"] = values

    async def execution_status(self, run):
        async with self.transaction(task_exclusive=False) as conn:
            row = (
                await conn.execute(
                    select(task_runs.c.cancel_requested_at).where(
                        task_runs.c.id == run["id"], task_runs.c.status == "running"
                    )
                )
            ).first()
            return {"running": row is not None, "cancel_requested": bool(row and row[0])}

    async def progress(self, run, result, *, max_chars):
        if len(result) > max_chars:
            raise ValueError("任务文字结果超过持久化上限。")
        async with self.transaction() as conn:
            updated = await conn.execute(
                update(task_runs)
                .where(task_runs.c.id == run["id"], task_runs.c.status == "running")
                .values(result_markdown=result, result_is_partial=True)
                .returning(task_runs)
            )
            row = updated.mappings().first()
            if row is not None:
                await sync_run(conn, dict(row))
            return row is not None

    async def finish(
        self, run, *, status, result="", error_code=None, error_message=None, max_chars=200_000, goal_complete=False
    ):
        if status not in {"succeeded", "failed", "cancelled"} or len(result) > max_chars:
            raise ValueError("无效任务终态或结果超过上限。")
        async with self.transaction() as conn:
            updated = await conn.execute(
                update(task_runs)
                .where(task_runs.c.id == run["id"], task_runs.c.status == "running")
                .values(
                    status=status,
                    finished_at=func.clock_timestamp(),
                    result_markdown=result,
                    result_is_partial=status != "succeeded",
                    error_code=error_code,
                    error_message=error_message,
                )
                .returning(task_runs)
            )
            row = updated.mappings().first()
            if row is not None:
                if status == "succeeded" and goal_complete:
                    await conn.execute(update(tasks).where(
                        tasks.c.id == row["task_id"], tasks.c.version == row["config_snapshot"]["task_version"],
                        tasks.c.status == "enabled",
                    ).values(status="paused", next_run_at=None, pause_reason="goal_completed", version=tasks.c.version + 1, updated_at=func.clock_timestamp()))
                await sync_run(conn, dict(row))
            return row is not None

    async def interrupt_running(self, error_code):
        messages = {
            "service_restarted": "Web 服务重启，本次执行已中断，未自动重试。",
            "service_shutdown": "Web 服务停止，本次执行已中断，未自动重试。",
        }
        message = messages[error_code]
        # 必须持有单实例锁；启动收敛旧进程，关闭收尾未能提交终态的本地任务。
        async with self.transaction() as conn:
            result = await conn.execute(
                update(task_runs)
                .where(task_runs.c.status == "running")
                .values(
                    status="failed",
                    finished_at=func.clock_timestamp(),
                    result_is_partial=True,
                    error_code=error_code,
                    error_message=message,
                )
                .returning(task_runs)
            )
            rows = result.mappings().all()
            for row in rows:
                await sync_run(conn, dict(row))
            return len(rows)

    async def expire_queued(self, timeout_seconds):
        async with self.transaction() as conn:
            timestamp = await conn.scalar(select(func.clock_timestamp()))
            result = await conn.execute(
                update(task_runs)
                .where(
                    task_runs.c.status == "queued",
                    task_runs.c.queued_at <= timestamp - timedelta(seconds=timeout_seconds),
                )
                .values(
                    status="failed",
                    finished_at=timestamp,
                    result_is_partial=True,
                    error_code="queue_timeout",
                    error_message="排队时间超过上限。",
                )
                .returning(task_runs)
            )
            rows = result.mappings().all()
            for row in rows:
                await sync_run(conn, dict(row))
            return len(rows)
