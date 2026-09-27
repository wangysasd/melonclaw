"""唯一租户用户仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, insert, select
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import tenants, users
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.repository.mappers import _now, _user_dict
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
            .where(users.c.tenant_status == "active")
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

    async def create_user(
        self,
        *,
        user_id: str,
        user_name_zh: str,
        tenant_id: str,
        tenant_role: str = "member",
    ) -> None:
        """新建一个归属指定租户的用户。

        user_id 是主键：调用方预查 ``user_exists`` 挡常规重复，这里再捕获
        并发插入撞主键的 IntegrityError，统一转成可读的 422，避免 500。
        """

        try:
            async with self.engine.begin() as connection:
                await connection.execute(
                    insert(users).values(
                        user_id=user_id,
                        tenant_id=tenant_id,
                        user_name_zh=user_name_zh,
                        tenant_role=tenant_role,
                        tenant_status="active",
                        created_at=_now(),
                    )
                )
        except IntegrityError as exc:
            raise ValueError(f"用户 {user_id!r} 已存在。") from exc

    async def get_user_context(self, user_id: str) -> UserContext | None:
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
            .where(users.c.user_id == user_id)
            .where(users.c.tenant_status == "active")
        )
        async with self.engine.connect() as connection:
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
        )
