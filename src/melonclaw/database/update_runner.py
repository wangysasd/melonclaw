"""执行已登记的数据库增量升级。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.database.database import Database
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.updates import UPDATES


async def apply_schema_updates(database: Database) -> tuple[str, ...]:
    """在同一事务中串行应用未执行的更新并记录成功项。"""

    applied_now: list[str] = []
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext(current_schema()), hashtext('melonclaw-db-update'))"
            )
        )
        base_tables = await connection.execute(
            text(
                "SELECT to_regclass('tenants'), to_regclass('users')"
            )
        )
        if any(value is None for value in base_tables.one()):
            raise DatabaseSchemaError(
                "未发现已有用户和租户表；首次初始化请运行 uv run melonclaw-db-init。"
            )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS melonclaw_schema_updates (
                    update_id VARCHAR(120) PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        result = await connection.execute(
            text("SELECT update_id FROM melonclaw_schema_updates")
        )
        applied = set(result.scalars().all())

        for update in sorted(UPDATES, key=lambda item: item.update_id):
            if update.update_id in applied:
                continue
            await update.upgrade(connection)
            await connection.execute(
                text(
                    "INSERT INTO melonclaw_schema_updates (update_id, description) "
                    "VALUES (:update_id, :description)"
                ),
                {
                    "update_id": update.update_id,
                    "description": update.description,
                },
            )
            applied_now.append(update.update_id)

    return tuple(applied_now)
