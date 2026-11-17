"""模型用途结构校验，初始化与升级共用。"""

from sqlalchemy import inspect

from melonclaw.database.errors import DatabaseSchemaError


async def verify_model_purpose(connection):
    def verify(sync):
        inspector = inspect(sync)
        columns = {c["name"]: c for c in inspector.get_columns("model_configs")}
        checks = {c["name"] for c in inspector.get_check_constraints("model_configs")}
        indexes = {i["name"]: i for i in inspector.get_indexes("model_configs")}
        expected = {
            "ux_model_configs_is_default": ["purpose"],
            "ux_model_configs_user_default": ["created_by", "purpose"],
        }
        valid = (
            "purpose" in columns
            and not columns["purpose"]["nullable"]
            and columns["context_window"]["nullable"]
            and {
                "ck_model_configs_purpose",
                "ck_model_configs_context_window",
                "ck_model_configs_embedding",
            }
            <= checks
        )
        for name, fields in expected.items():
            index = indexes.get(name, {})
            valid = valid and index.get("column_names") == fields and index.get("unique")
            predicate = str(index.get("dialect_options", {}).get("postgresql_where", ""))
            valid = valid and "is_default" in predicate and "scope" in predicate
        if not valid:
            raise DatabaseSchemaError(
                "模型用途结构不匹配，请停止 Web 后运行 uv run melonclaw-db-update。"
            )

    await connection.run_sync(verify)
