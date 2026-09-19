"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.database.constants import DEFAULT_PROJECT_NAME
from melonclaw.database.database import Database
from melonclaw.repository.mappers import _default_project_id, _now
from melonclaw.repository.seed_data import TENANT_SEEDS, USER_SEEDS, USER_TENANT_SEEDS


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

        result = await connection.execute(
            text("SELECT user_id FROM users ORDER BY user_id")
        )
        project_rows = []
        for raw_user_id in result.scalars().all():
            user_id = str(raw_user_id)
            project_id = _default_project_id(user_id)
            project_rows.append(
                {
                    "id": project_id,
                    "user_id": user_id,
                    "name": DEFAULT_PROJECT_NAME,
                    "workdir_path": f"projects/{project_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
        # 默认 Project 的 ID 由 user_id 推导，重复执行只会命中同一行。
        await connection.execute(
            text(
                "INSERT INTO projects "
                "(id, user_id, name, workdir_path, created_at, updated_at, "
                "status, is_default) "
                "VALUES (:id, :user_id, :name, :workdir_path, :created_at, "
                ":updated_at, 'active', TRUE) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, "
                "status = 'active', updated_at = EXCLUDED.updated_at"
            ),
            project_rows,
        )
