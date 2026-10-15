"""任务管理事务、权限查询与幂等账本。所有任务写入共享同一并发边界。"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, func, insert, null, select, text, tuple_, update

from melonclaw.core.task_errors import TaskError, task_not_found
from melonclaw.core.task_messages import task_conversation, task_setup_message
from melonclaw.core.task_schedule import TaskSchedule, next_run
from melonclaw.core.task_snapshot import task_thread_id
from melonclaw.database.schema import (
    chat_conversations,
    chat_messages,
    task_operations,
    task_runs,
    tasks,
    tenants,
    users,
)
from melonclaw.repository.models import UserContext
from melonclaw.repository.task_delivery import append_run, sync_run

ACTIVE = ("queued", "running")


def now():
    return datetime.now(UTC)


def json_value(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda x: x.isoformat() if isinstance(x, (date, datetime)) else str(x),
        )
    )


def scope(table, user_id):
    tenant = (
        select(users.c.tenant_id)
        .join(tenants)
        .where(
            users.c.user_id == user_id,
            users.c.tenant_status == "active",
            tenants.c.enabled.is_(True),
        )
        .scalar_subquery()
    )
    return (table.c.owner_user_id == user_id, table.c.tenant_id == tenant)


async def task_lock(conn):
    # 与账户管理同一锁：账户变更不能穿过任务保存/领取。
    await conn.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(current_schema()), 741923)")
    )
    # 短事务全局锁统一配额/归档/领取/终态；不持有到模型执行结束。
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema()), 741924)"))


class TaskTransaction:
    def __init__(self, conn, user, replay=None):
        self.conn, self.user, self.replay = conn, user, replay
        self.result = None

    async def current_time(self):
        return await self.conn.scalar(select(func.clock_timestamp()))

    async def task(self, task_id):
        row = (
            (
                await self.conn.execute(
                    select(tasks)
                    .where(
                        tasks.c.id == task_id,
                        *scope(tasks, self.user.user_id),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise task_not_found()
        return dict(row)

    async def save(self, definition, *, enabled, current=None, conversation_id=None,
                   record_setup=True):
        timestamp = await self.current_time()
        schedule = definition.schedule
        previous = (
            TaskSchedule.model_validate(current["definition"]["schedule"]) if current else None
        )
        if previous != schedule:
            schedule.validate_future(timestamp)
        resumed = current and current["status"] == "paused" and enabled
        next_at = (
            next_run(schedule, timestamp)
            if previous != schedule or resumed
            else current["next_run_at"]
        )
        values = dict(
            name=definition.name,
            definition=definition.model_dump(mode="json"),
            status="enabled" if enabled else "paused",
            pause_reason=None,
            next_run_at=next_at,
            updated_at=timestamp,
        )
        if current is None:
            values.update(
                id=uuid4(),
                owner_user_id=self.user.user_id,
                tenant_id=self.user.tenant_id,
                version=1,
                created_at=timestamp,
            )
            if conversation_id is None:
                conversation = task_conversation(values)
                await self.conn.execute(insert(chat_conversations).values(**conversation))
                conversation_id = conversation["id"]
            values["conversation_id"] = conversation_id
            if record_setup:
                await self.conn.execute(insert(chat_messages).values(**task_setup_message(values)))
            query = insert(tasks).values(**values).returning(tasks)
        else:
            query = (
                update(tasks)
                .where(tasks.c.id == current["id"])
                .values(
                    **values,
                    version=current["version"] + 1,
                )
                .returning(tasks)
            )
        saved = dict((await self.conn.execute(query)).mappings().one())
        if current is None or current["name"] != saved["name"]:
            titles = {"新会话"}
            if current is not None:
                titles.add(task_conversation(current)["title"])
            await self.conn.execute(update(chat_conversations).where(
                chat_conversations.c.id == saved["conversation_id"],
                chat_conversations.c.user_id == self.user.user_id,
                chat_conversations.c.title.in_(titles),
            ).values(title=task_conversation(saved)["title"], updated_at=timestamp))
        if record_setup and current is not None and current["definition"] != saved["definition"]:
            await self.conn.execute(select(chat_conversations.c.id).where(chat_conversations.c.id == saved["conversation_id"]).with_for_update())
            seq = await self.conn.scalar(select(func.coalesce(func.max(chat_messages.c.seq), 0)).where(chat_messages.c.conversation_id == saved["conversation_id"]))
            message = task_setup_message(saved, seq=int(seq) + 1)
            message["created_at"] = saved["updated_at"]
            await self.conn.execute(insert(chat_messages).values(**message))
            await self.conn.execute(update(chat_conversations).where(chat_conversations.c.id == saved["conversation_id"]).values(updated_at=saved["updated_at"]))
        return saved

    async def advance_schedule(self, task_id, next_at):
        await self.conn.execute(
            update(tasks).where(tasks.c.id == task_id).values(next_run_at=next_at)
        )

    async def enqueue(self, task, snapshot, *, scheduled_for=None):
        if (
            await self.conn.execute(
                select(task_runs.c.id).where(
                    task_runs.c.task_id == task["id"],
                    task_runs.c.status.in_(ACTIVE),
                )
            )
        ).first():
            raise TaskError("task_busy", "该任务已有排队或运行中的记录。")
        from melonclaw.repository.task_history import capture_history

        snapshot = dict(snapshot) | {"history": await capture_history(self.conn, self.user.user_id, task["conversation_id"])}
        run_id, timestamp = uuid4(), now()
        row = (
            (
                await self.conn.execute(
                    insert(task_runs)
                    .values(
                        id=run_id,
                        task_id=task["id"],
                        owner_user_id=task["owner_user_id"],
                        tenant_id=task["tenant_id"],
                        trigger_type="schedule" if scheduled_for else "manual",
                        scheduled_for=scheduled_for,
                        status="queued",
                        config_snapshot=snapshot,
                        execution_context_id=task_thread_id(run_id),
                        queued_at=timestamp,
                        created_at=timestamp,
                    )
                    .returning(task_runs)
                )
            )
            .mappings()
            .one()
        )
        run = dict(row)
        await append_run(self.conn, task, run)
        return run | {"conversation_id": task["conversation_id"]}

    async def run(self, run_id):
        row = (
            (
                await self.conn.execute(
                    select(task_runs)
                    .where(
                        task_runs.c.id == run_id,
                        *scope(task_runs, self.user.user_id),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise task_not_found()
        return dict(row)

    async def cancel(self, run):
        if run["status"] not in ACTIVE:
            return run
        values = {"cancel_requested_at": run["cancel_requested_at"] or now()}
        if run["status"] == "queued":
            values.update(status="cancelled", finished_at=now(), result_is_partial=True)
        updated = dict((await self.conn.execute(
            update(task_runs).where(task_runs.c.id == run["id"]).values(**values).returning(task_runs)
        )).mappings().one())
        await sync_run(self.conn, updated)
        return updated

    async def archive(self, task):
        cancelled = await self.conn.execute(
            update(task_runs)
            .where(
                task_runs.c.task_id == task["id"],
                task_runs.c.status == "queued",
            )
            .values(status="cancelled", finished_at=now(), result_is_partial=True).returning(task_runs)
        )
        for run in cancelled.mappings():
            await sync_run(self.conn, dict(run))
        return dict(
            (
                await self.conn.execute(
                    update(tasks)
                    .where(tasks.c.id == task["id"])
                    .values(
                        status="archived",
                        version=task["version"] + 1,
                        next_run_at=None,
                        updated_at=now(),
                    )
                    .returning(tasks)
                )
            )
            .mappings()
            .one()
        )


class TaskRepository:
    def __init__(self, engine, *, operation_retention_days=30):
        self.engine = engine
        self.operation_retention_days = operation_retention_days

    @asynccontextmanager
    async def operation(self, user_id, key, request):
        digest = hashlib.sha256(
            json.dumps(json_value(request), sort_keys=True).encode()
        ).hexdigest()
        async with self.engine.begin() as conn:
            await task_lock(conn)
            row = (
                (
                    await conn.execute(
                        select(users, tenants.c.tenant_name_zh)
                        .join(tenants)
                        .where(
                            users.c.user_id == user_id,
                            users.c.tenant_status == "active",
                            tenants.c.enabled.is_(True),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise task_not_found()
            user = UserContext(
                user_id,
                row["user_name_zh"],
                row["tenant_id"],
                row["tenant_name_zh"],
                row["tenant_role"],
                row["tenant_status"],
            )
            prior = (
                (
                    await conn.execute(
                        select(task_operations).where(
                            task_operations.c.owner_user_id == user_id,
                            task_operations.c.tenant_id == user.tenant_id,
                            task_operations.c.idempotency_key == key,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if prior and prior["request_digest"] != digest:
                raise TaskError("idempotency_conflict", "该请求标识已用于不同操作，请重新提交。")
            if prior:
                timestamp = await conn.scalar(select(func.clock_timestamp()))
                if prior["response"] is None or prior["created_at"] <= timestamp - timedelta(days=self.operation_retention_days):
                    raise TaskError("idempotency_expired", "该操作回执已过保留期，请先核实当前状态，再发起新的操作。")
            tx = TaskTransaction(conn, user, prior["response"] if prior else None)
            yield tx
            if prior is None:
                if tx.result is None:
                    raise RuntimeError("任务操作缺少事务结果。")
                await conn.execute(
                    insert(task_operations).values(
                        owner_user_id=user_id,
                        tenant_id=user.tenant_id,
                        idempotency_key=key,
                        request_digest=digest,
                        response=json_value(tx.result),
                        created_at=now(),
                    )
                )

    async def get_task(self, user_id, task_id):
        async with self.engine.connect() as conn:
            row = await conn.execute(
                select(tasks, active_exists()).where(tasks.c.id == task_id, *scope(tasks, user_id))
            )
            value = row.mappings().first()
        if not value:
            raise task_not_found()
        return dict(value)

    async def list_tasks(self, user_id, *, name="", status=None, page=1, page_size=20):
        conditions = [*scope(tasks, user_id)]
        if name:
            conditions.append(tasks.c.name.icontains(name, autoescape=True))
        if status:
            conditions.append(tasks.c.status == status)
        async with self.engine.connect() as conn:
            total = await conn.scalar(select(func.count()).select_from(tasks).where(*conditions))
            rows = [
                dict(r)
                for r in (
                    await conn.execute(
                        select(tasks, active_exists())
                        .where(*conditions)
                        .order_by(
                            case({"enabled": 0, "paused": 1, "archived": 2},
                                 value=tasks.c.status, else_=3),
                            tasks.c.updated_at.desc(), tasks.c.id.desc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                ).mappings()
            ]
            latest = (
                await conn.execute(
                    select(*run_summary_columns())
                    .where(
                        task_runs.c.task_id.in_([r["id"] for r in rows]),
                    )
                    .distinct(task_runs.c.task_id)
                    .order_by(
                        task_runs.c.task_id, task_runs.c.created_at.desc(), task_runs.c.id.desc()
                    )
                )
            ).mappings()
            lookup = {r["task_id"]: dict(r) for r in latest}
            for row in rows:
                row["latest_run"] = lookup.get(row["id"])
        return {"items": rows, "total": total}

    async def runs(self, user_id, task_id, *, page=1, page_size=20):
        await self.get_task(user_id, task_id)
        conditions = [task_runs.c.task_id == task_id, *scope(task_runs, user_id)]
        async with self.engine.connect() as conn:
            total = await conn.scalar(
                select(func.count()).select_from(task_runs).where(*conditions)
            )
            rows = (
                await conn.execute(
                    select(*run_summary_columns())
                    .where(*conditions)
                    .order_by(task_runs.c.created_at.desc(), task_runs.c.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).mappings()
            return {"items": [dict(r) for r in rows], "total": total}

    async def get_run(self, user_id, run_id):
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(task_runs, tasks.c.conversation_id).join(tasks).where(
                            task_runs.c.id == run_id, *scope(task_runs, user_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise task_not_found()
        return dict(row)

    async def retire_operations(self, *, at=None, limit=100):
        """分批移除过期的大回执；拒重键永久保留，旧请求不能再次执行。"""
        async with self.engine.begin() as conn:
            await task_lock(conn)
            timestamp = at or await conn.scalar(select(func.clock_timestamp()))
            columns = (task_operations.c.owner_user_id, task_operations.c.tenant_id,
                       task_operations.c.idempotency_key)
            expired = select(*columns).where(
                task_operations.c.created_at <= timestamp - timedelta(days=self.operation_retention_days),
                task_operations.c.response.is_not(None),
            ).order_by(task_operations.c.created_at).limit(limit)
            result = await conn.execute(
                update(task_operations).where(tuple_(*columns).in_(expired))
                .values(response=null()).returning(task_operations.c.idempotency_key)
            )
            return len(result.all())


def run_summary_columns():
    return [
        task_runs.c[name]
        for name in (
            "id",
            "task_id",
            "trigger_type",
            "status",
            "queued_at",
            "started_at",
            "finished_at",
            "created_at",
            "error_code",
            "result_is_partial",
            "cancel_requested_at",
            "scheduled_for",
        )
    ]


def active_exists():
    return (
        select(task_runs.c.id)
        .where(task_runs.c.task_id == tasks.c.id, task_runs.c.status.in_(ACTIVE))
        .correlate(tasks)
        .exists()
        .label("has_active_run")
    )
