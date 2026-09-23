"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.database.database import Database
from melonclaw.repository.mappers import _now
from melonclaw.repository.seed_data import TENANT_SEEDS, USER_SEEDS, USER_TENANT_SEEDS


async def seed_demo_data(database: Database) -> None:
    """幂等写入演示租户、用户、关系。"""

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
                "INSERT INTO user_tenants (user_id, tenant_id, created_at) "
                "VALUES (:user_id, :tenant_id, :created_at) "
                "ON CONFLICT (user_id, tenant_id) DO NOTHING"
            ),
            [
                {
                    **item,
                    "created_at": timestamp,
                }
                for item in USER_TENANT_SEEDS
            ],
        )
