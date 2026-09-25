"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.database.database import Database
from melonclaw.repository.errors import SeedDataConflictError
from melonclaw.repository.mappers import _now
from melonclaw.repository.seed_data import TENANT_SEEDS, USER_SEEDS


async def seed_demo_data(database: Database) -> None:
    """幂等写入演示租户和各自唯一归属的用户。"""

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
        for item in USER_SEEDS:
            # PostgreSQL 在冲突行不满足 UPDATE WHERE 时不会更新，也不会把该行放进 RETURNING。
            # 因此无返回行表示既有用户已归属另一租户，不能在重跑种子时静默改归属。
            result = await connection.execute(
                text(
                    "INSERT INTO users "
                    "(user_id, tenant_id, user_name_zh, tenant_role, tenant_status, created_at) "
                    "VALUES "
                    "(:user_id, :tenant_id, :user_name_zh, :tenant_role, :tenant_status, :created_at) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "user_name_zh = EXCLUDED.user_name_zh "
                    "WHERE users.tenant_id = EXCLUDED.tenant_id "
                    "RETURNING user_id"
                ),
                {**item, "created_at": timestamp},
            )
            if result.scalar_one_or_none() is None:
                raise SeedDataConflictError(str(item["user_id"]))
