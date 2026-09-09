"""Memory 控制面审计事件仓储。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, select

from melonclaw.database.schema import memory_events
from melonclaw.repository.mappers import _now


class MemoryEventRepositoryMixin:
    async def record_memory_event(
        self,
        *,
        scope_type: str,
        scope_id: str,
        agent_id: str,
        key: str,
        operation: str,
        actor_user_id: str | None,
        tenant_id: str | None,
        request_id: str | None,
        operation_id: str | None = None,
        run_id: str | None,
        version: int,
        content_hash: str | None,
        event_metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """记录 Memory 控制面事件；内容本身仍以 Store 为事实源。"""

        event_id = uuid4()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(memory_events).values(
                    event_id=event_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    agent_id=agent_id,
                    key=key,
                    operation=operation,
                    actor_user_id=actor_user_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    operation_id=operation_id,
                    run_id=run_id,
                    version=version,
                    content_hash=content_hash,
                    metadata=event_metadata or {},
                    created_at=_now(),
                )
            )
        return event_id

    async def find_memory_event(
        self,
        *,
        scope_type: str,
        scope_id: str,
        agent_id: str,
        key: str,
        request_id: str,
        operation: str,
        operation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """按操作级 ID 查找已成功记录的 Memory 操作。"""

        conditions = [
            memory_events.c.scope_type == scope_type,
            memory_events.c.scope_id == scope_id,
            memory_events.c.agent_id == agent_id,
            memory_events.c.key == key,
            memory_events.c.request_id == request_id,
            memory_events.c.operation == operation,
        ]
        if operation_id is not None:
            conditions.append(memory_events.c.operation_id == operation_id)
        else:
            conditions.append(memory_events.c.operation_id.is_(None))
        query = (
            select(memory_events)
            .where(and_(*conditions))
            .order_by(memory_events.c.created_at.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return dict(row) if row else None


