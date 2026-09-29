"""业务 Schema 初始化和完整性检查。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.database.constants import (
    BUSINESS_TABLES,
    CHECKPOINT_TABLES,
    STORE_TABLES,
)
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.schema import metadata


class SchemaMigrationMixin:
    async def create_schema(self) -> None:
        """由独立初始化命令调用；API lifespan 不会自动建表。"""

        async with self.engine.begin() as connection:
            # 数据库按“可清空重建”维护：schema.py 是唯一事实来源，
            # create_all 按外键拓扑顺序建表，索引、约束和部分唯一索引一并创建。
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(sync_connection)
            )
        # create_all 不会修补已有表。若用户没有按约定清空旧库，
        # 在写入演示数据前就给出明确的重建提示。
        await self.verify_schema(require_checkpointer=False, require_store=False)

    async def verify_schema(
        self,
        *,
        require_checkpointer: bool = True,
        require_store: bool = False,
    ) -> None:
        expected = list(BUSINESS_TABLES)
        if require_checkpointer:
            expected.extend(CHECKPOINT_TABLES)
        if require_store:
            expected.extend(STORE_TABLES)
        placeholders = ", ".join(f":table_{index}" for index in range(len(expected)))
        query = text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND (table_name IN ("
            + placeholders
            + ") OR table_name = 'schema_migrations')"
        )
        bind_values = {f"table_{index}": name for index, name in enumerate(expected)}
        async with self.engine.connect() as connection:
            result = await connection.execute(query, bind_values)
            found = {str(row[0]) for row in result.fetchall()}
        if "schema_migrations" in found:
            raise DatabaseSchemaError(
                "数据库仍包含已废弃的 schema_migrations 表。"
                "请清空/重建数据库后重新运行 "
                "uv run melonclaw-db-init。"
            )
        missing = [name for name in expected if name not in found]
        if missing:
            required = "、".join(missing)
            raise DatabaseSchemaError(
                f"数据库尚未初始化，缺少表：{required}。请先运行 uv run melonclaw-db-init。"
            )

        business_placeholders = ", ".join(
            f":business_{index}" for index in range(len(BUSINESS_TABLES))
        )
        column_query = text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name IN ("
            + business_placeholders
            + ")"
        )
        business_values = {
            f"business_{index}": name
            for index, name in enumerate(BUSINESS_TABLES)
        }
        async with self.engine.connect() as connection:
            column_result = await connection.execute(column_query, business_values)
            actual_columns: dict[str, set[str]] = {
                table_name: set() for table_name in BUSINESS_TABLES
            }
            for table_name, column_name in column_result.fetchall():
                actual_columns[str(table_name)].add(str(column_name))

        mismatches: list[str] = []
        for table_name in BUSINESS_TABLES:
            expected_columns = set(metadata.tables[table_name].columns.keys())
            actual = actual_columns[table_name]
            missing_columns = sorted(expected_columns - actual)
            extra_columns = sorted(actual - expected_columns)
            if missing_columns:
                mismatches.append(
                    f"{table_name} 缺少列 {', '.join(missing_columns)}"
                )
            if extra_columns:
                mismatches.append(
                    f"{table_name} 存在旧列 {', '.join(extra_columns)}"
                )
        if mismatches:
            details = "；".join(mismatches)
            raise DatabaseSchemaError(
                "数据库结构与当前 schema.py 不一致："
                f"{details}。请清空/重建数据库后重新运行 "
                "uv run melonclaw-db-init。"
            )

        # create_all 不会更新已有 CHECK；旧附件表会在 ZIP 上传时才拒绝 archive。
        constraint_query = text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'public.chat_attachments'::regclass "
            "AND conname = 'ck_chat_attachments_kind'"
        )
        async with self.engine.connect() as connection:
            constraint_rows = (await connection.execute(constraint_query, {})).fetchall()
        if len(constraint_rows) != 1 or "archive" not in str(constraint_rows[0][0]):
            raise DatabaseSchemaError(
                "chat_attachments 的附件类型约束仍是旧版本，ZIP 无法上传。"
                "请按 README 停服重建附件两表，再运行 uv run melonclaw-db-init。"
            )
