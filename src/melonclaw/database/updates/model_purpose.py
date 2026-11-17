"""已有模型表补上 purpose。

现有行记为 chat，保留原 ID、启用状态和默认标记。上下文窗口改为可空，
并换成按用途区分的约束；默认模型唯一索引改为按 purpose 区分。
purpose 已存在且结构匹配时直接通过。列已存在但结构不一致时失败并回滚。
"""

from sqlalchemy import inspect, text

from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.model_ddl import verify_model_purpose


async def upgrade(connection):
    def prepared(sync):
        inspector = inspect(sync)
        columns = {column["name"]: column for column in inspector.get_columns("model_configs")}
        return "purpose" in columns

    if await connection.run_sync(prepared):
        await verify_model_purpose(connection)
        return

    unsafe = await connection.scalar(text(
        "SELECT COUNT(*) FROM model_configs "
        "WHERE context_window IS NULL OR context_window <= 0 "
        "OR length(btrim(model_name)) = 0"
    ))
    if unsafe:
        raise DatabaseSchemaError(
            "存在无法归为 chat 的模型，升级未修改 model_configs。"
        )

    await connection.execute(text(
        "ALTER TABLE model_configs "
        "ADD COLUMN purpose VARCHAR(24) NOT NULL DEFAULT 'chat'"
    ))
    await connection.execute(text(
        "ALTER TABLE model_configs DROP CONSTRAINT ck_model_configs_context_window"
    ))
    await connection.execute(text(
        "ALTER TABLE model_configs ALTER COLUMN context_window DROP NOT NULL"
    ))
    await connection.execute(text(
        "ALTER TABLE model_configs ADD CONSTRAINT ck_model_configs_purpose "
        "CHECK (purpose IN ('chat','embedding','document_parser') "
        "AND (purpose = 'chat' OR scope = 'global'))"
    ))
    await connection.execute(text(
        "ALTER TABLE model_configs ADD CONSTRAINT ck_model_configs_context_window "
        "CHECK ((purpose IN ('chat') AND context_window IS NOT NULL AND context_window > 0) "
        "OR (purpose IN ('embedding','document_parser') AND context_window IS NULL))"
    ))
    await connection.execute(text(
        "ALTER TABLE model_configs ADD CONSTRAINT ck_model_configs_embedding "
        "CHECK (purpose <> 'embedding' OR length(btrim(model_name)) > 0)"
    ))
    await connection.execute(text("DROP INDEX ux_model_configs_is_default"))
    await connection.execute(text("DROP INDEX ux_model_configs_user_default"))
    await connection.execute(text(
        "CREATE UNIQUE INDEX ux_model_configs_is_default ON model_configs (purpose) "
        "WHERE is_default IS TRUE AND scope = 'global'"
    ))
    await connection.execute(text(
        "CREATE UNIQUE INDEX ux_model_configs_user_default "
        "ON model_configs (created_by, purpose) "
        "WHERE is_default IS TRUE AND scope = 'user'"
    ))
    await verify_model_purpose(connection)
