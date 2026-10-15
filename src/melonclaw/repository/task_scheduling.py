"""调度、跳过与计划推进的同一事务；清理严格限定终态任务的精确 thread。"""

from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import func, select, update

from melonclaw.core.task_snapshot import task_thread_id
from melonclaw.database.schema import task_runs, tasks, tenants, users
from melonclaw.repository.models import UserContext
from melonclaw.repository.tasks import ACTIVE, TaskRepository, TaskTransaction, task_lock


class TaskScheduleRepository(TaskRepository):
    @asynccontextmanager
    async def due(self, *, at=None):
        async with self.engine.begin() as conn:
            await task_lock(conn)
            timestamp = at or await conn.scalar(select(func.clock_timestamp()))
            row = (
                (
                    await conn.execute(
                        select(tasks)
                        .join(users, tasks.c.owner_user_id == users.c.user_id)
                        .join(tenants, tenants.c.tenant_id == tasks.c.tenant_id)
                        .where(
                            tasks.c.status == "enabled",
                            tasks.c.next_run_at <= timestamp,
                            users.c.tenant_id == tasks.c.tenant_id,
                            users.c.tenant_status == "active",
                            tenants.c.enabled.is_(True),
                        )
                        .order_by(tasks.c.next_run_at, tasks.c.id)
                        .limit(1)
                        .with_for_update(of=tasks)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                yield None
                return
            task = dict(row)
            user = (
                (
                    await conn.execute(
                        select(users, tenants.c.tenant_name_zh)
                        .join(tenants)
                        .where(users.c.user_id == task["owner_user_id"])
                    )
                )
                .mappings()
                .one()
            )
            context = UserContext(
                user["user_id"],
                user["user_name_zh"],
                user["tenant_id"],
                user["tenant_name_zh"],
                user["tenant_role"],
                user["tenant_status"],
            )
            active = bool(
                await conn.scalar(
                    select(task_runs.c.id)
                    .where(
                        task_runs.c.task_id == task["id"],
                        task_runs.c.status.in_(ACTIVE),
                    )
                    .limit(1)
                )
            )
            yield TaskTransaction(conn, context), task, timestamp, active

    async def cleanup_candidates(self, retention_days, *, at=None):
        async with self.engine.connect() as conn:
            timestamp = at or await conn.scalar(select(func.clock_timestamp()))
            rows = (
                (
                    await conn.execute(
                        select(task_runs.c.id, task_runs.c.execution_context_id)
                        .where(
                            task_runs.c.status.in_(["succeeded", "failed", "cancelled"]),
                            task_runs.c.finished_at < timestamp - timedelta(days=retention_days),
                            task_runs.c.checkpoint_cleaned_at.is_(None),
                        )
                        .order_by(task_runs.c.finished_at)
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            return [
                dict(row)
                for row in rows
                if row["execution_context_id"] == task_thread_id(row["id"])
            ]

    async def mark_cleaned(self, run_id):
        async with self.engine.begin() as conn:
            await conn.execute(
                update(task_runs)
                .where(
                    task_runs.c.id == run_id,
                    task_runs.c.status.not_in(ACTIVE),
                    task_runs.c.checkpoint_cleaned_at.is_(None),
                )
                .values(checkpoint_cleaned_at=func.clock_timestamp())
            )
