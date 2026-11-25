"""唯一租户用户仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, select

from melonclaw.database.schema import tenants, users
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.repository.mappers import _user_dict
from melonclaw.repository.models import UserContext


class UserRepositoryMixin:
    async def list_users(self) -> list[dict[str, Any]]:
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                users.c.tenant_id,
                tenants.c.tenant_name_zh,
                users.c.tenant_role,
                users.c.tenant_status,
            )
            .select_from(users.join(tenants, users.c.tenant_id == tenants.c.tenant_id))
            .where(users.c.tenant_status == "active", tenants.c.enabled.is_(True))
            .order_by(
                case((users.c.user_id == DEFAULT_SIMULATED_USER_ID, 0), else_=1),
                users.c.user_id.asc(),
            )
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_user_dict(row) for row in rows]

    async def user_exists(self, user_id: str) -> bool:
        query = select(users.c.user_id).where(users.c.user_id == user_id)
        async with self.engine.connect() as connection:
            return (await connection.execute(query)).first() is not None

    async def get_user_context(self, user_id: str, *, connection=None) -> UserContext | None:
        return await self._active_user(user_id, require_enabled_tenant=True, connection=connection)

    async def active_user(self, user_id: str, *, connection=None) -> UserContext | None:
        """读取 JWT 身份及当前租户状态。"""

        return await self._active_user(user_id, require_enabled_tenant=False, connection=connection)

    async def _active_user(self, user_id: str, *, require_enabled_tenant: bool, connection):
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                users.c.tenant_id,
                tenants.c.tenant_name_zh,
                users.c.tenant_role,
                users.c.tenant_status,
                tenants.c.enabled.label("tenant_enabled"),
            )
            .select_from(users.join(tenants, users.c.tenant_id == tenants.c.tenant_id))
            .where(users.c.user_id == user_id, users.c.tenant_status == "active")
        )
        if require_enabled_tenant:
            query = query.where(tenants.c.enabled.is_(True))
        return await self._user_context(query, connection)

    async def _user_context(self, query, connection) -> UserContext | None:
        if connection is None:
            async with self.engine.connect() as conn:
                return await self._user_context(query, conn)
        row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        return UserContext(
            user_id=str(row["user_id"]),
            user_name_zh=str(row["user_name_zh"]),
            tenant_id=str(row["tenant_id"]),
            tenant_name_zh=str(row["tenant_name_zh"]),
            tenant_role=str(row["tenant_role"]),
            tenant_status=str(row["tenant_status"]),
            tenant_enabled=bool(row["tenant_enabled"]),
        )
