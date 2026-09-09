"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from melonclaw.database.constants import DEFAULT_PROJECT_NAME
from melonclaw.database.database import Database
from melonclaw.repository.seed_data import TENANT_SEEDS, USER_SEEDS, USER_TENANT_SEEDS


def _now() -> datetime:
    return datetime.now(UTC)


def _user_tenant_id(user_id: str, tenant_id: str):
    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:user-tenant:{user_id}:{tenant_id}",
    )


def _default_project_id(user_id: str):
    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:default-project:{user_id}",
    )


async def seed_demo_data(database: Database) -> None:
    """幂等写入演示租户、用户、关系和默认 Project。"""

    async with database.engine.begin() as connection:
        timestamp = _now()
        await connection.execute(
            text(
                "INSERT INTO tenants "
                "(tenant_id, tenant_name_zh, created_at) "
                "VALUES (:tenant_id, :tenant_name_zh, :created_at) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "tenant_name_zh = EXCLUDED.tenant_name_zh"
            ),
            [{**item, "created_at": timestamp} for item in TENANT_SEEDS],
        )
        await connection.execute(
            text(
                "INSERT INTO users (user_id, user_name_zh, created_at) "
                "VALUES (:user_id, :user_name_zh, :created_at) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "user_name_zh = EXCLUDED.user_name_zh"
            ),
            [{**item, "created_at": timestamp} for item in USER_SEEDS],
        )
        await connection.execute(
            text(
                "INSERT INTO user_tenants (id, user_id, tenant_id, created_at) "
                "VALUES (:id, :user_id, :tenant_id, :created_at) "
                "ON CONFLICT (user_id, tenant_id) DO NOTHING"
            ),
            [
                {
                    **item,
                    "id": _user_tenant_id(item["user_id"], item["tenant_id"]),
                    "created_at": timestamp,
                }
                for item in USER_TENANT_SEEDS
            ],
        )

        result = await connection.execute(
            text("SELECT user_id FROM users ORDER BY user_id")
        )
        for raw_user_id in result.scalars().all():
            user_id = str(raw_user_id)
            existing = await connection.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE user_id = :user_id AND is_default "
                    "ORDER BY id LIMIT 1"
                ),
                {"user_id": user_id},
            )
            existing_id = existing.scalar()
            if existing_id is not None:
                await connection.execute(
                    text(
                        "UPDATE projects SET name = :name, status = 'active', "
                        "updated_at = :updated_at WHERE id = :id"
                    ),
                    {
                        "id": existing_id,
                        "name": DEFAULT_PROJECT_NAME,
                        "updated_at": timestamp,
                    },
                )
                continue
            project_id = _default_project_id(user_id)
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, name, workdir_path, created_at, updated_at, "
                    "status, is_default) "
                    "VALUES (:id, :user_id, :name, :workdir_path, :created_at, "
                    ":updated_at, 'active', TRUE) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": project_id,
                    "user_id": user_id,
                    "name": DEFAULT_PROJECT_NAME,
                    "workdir_path": f"projects/{project_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
