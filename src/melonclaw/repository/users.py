"""用户和租户关系仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, select

from melonclaw.database.schema import tenants, user_tenants, users
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.repository.mappers import _user_dict
from melonclaw.repository.models import UserContext


class UserRepositoryMixin:
    async def list_users(self) -> list[dict[str, Any]]:
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                tenants.c.tenant_id,
                tenants.c.tenant_name_zh,
                user_tenants.c.role,
                user_tenants.c.status,
            )
            .select_from(
                users.join(
                    user_tenants,
                    users.c.user_id == user_tenants.c.user_id,
                ).join(
                    tenants,
                    user_tenants.c.tenant_id == tenants.c.tenant_id,
                )
            )
            .where(user_tenants.c.status == "active")
            .order_by(
                case(
                    (users.c.user_id == DEFAULT_SIMULATED_USER_ID, 0),
                    else_=1,
                ),
                users.c.user_id.asc(),
                tenants.c.tenant_id.asc(),
            )
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_id = str(row["user_id"])
            item = grouped.setdefault(
                user_id,
                {
                    "row": row,
                    "tenant_ids": [],
                    "tenant_names": [],
                    "tenant_memberships": [],
                },
            )
            item["tenant_ids"].append(str(row["tenant_id"]))
            item["tenant_names"].append(str(row["tenant_name_zh"]))
            item["tenant_memberships"].append(
                {
                    "tenant_id": str(row["tenant_id"]),
                    "tenant_name": str(row["tenant_name_zh"]),
                    "role": str(row.get("role") or "member"),
                    "status": str(row.get("status") or "active"),
                }
            )
        return [
            _user_dict(
                item["row"],
                tenant_ids=item["tenant_ids"],
                tenant_names=item["tenant_names"],
                tenant_memberships=item["tenant_memberships"],
            )
            for item in grouped.values()
        ]

    async def get_user_context(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> UserContext | None:
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                tenants.c.tenant_id,
                tenants.c.tenant_name_zh,
                user_tenants.c.role,
                user_tenants.c.status,
            )
            .select_from(
                users.join(
                    user_tenants,
                    users.c.user_id == user_tenants.c.user_id,
                ).join(
                    tenants,
                    user_tenants.c.tenant_id == tenants.c.tenant_id,
                )
            )
            .where(users.c.user_id == user_id)
            .where(user_tenants.c.status == "active")
            .order_by(user_tenants.c.tenant_id.asc())
        )
        if tenant_id is not None:
            query = query.where(user_tenants.c.tenant_id == tenant_id)
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        return UserContext(
            user_id=str(row["user_id"]),
            user_name_zh=str(row["user_name_zh"]),
            tenant_id=str(row["tenant_id"]),
            tenant_name_zh=str(row["tenant_name_zh"]),
            tenant_role=str(row.get("role") or "member"),
            tenant_status=str(row.get("status") or "active"),
        )

