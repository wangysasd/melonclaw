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


class SchemaValidationMixin:
    async def create_schema(self) -> None:
        """由独立初始化命令调用；API lifespan 不会自动建表。"""

        async with self.engine.begin() as connection:
            # db-init 仅用于首次初始化或重大变更后的空库重建。
            # 常规保留数据升级由 melonclaw-db-update 单独执行。
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(sync_connection)
            )
        # create_all 只创建缺失对象，不会修补已有表；增量变更由 db-update 执行。
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
            "WHERE table_schema = 'public' AND table_name IN ("
            + placeholders
            + ")"
        )
        bind_values = {f"table_{index}": name for index, name in enumerate(expected)}
        async with self.engine.connect() as connection:
            result = await connection.execute(query, bind_values)
            found = {str(row[0]) for row in result.fetchall()}
        missing = [name for name in expected if name not in found]
        if missing:
            required = "、".join(missing)
            if {"tenants", "users"}.issubset(found):
                command = "uv run melonclaw-db-update"
                message = "数据库需要应用增量更新"
            else:
                command = "uv run melonclaw-db-init"
                message = "数据库尚未完成首次初始化"
            raise DatabaseSchemaError(
                f"{message}，缺少表：{required}。请运行 {command}。"
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
                    f"{table_name} 存在未定义列 {', '.join(extra_columns)}"
                )
        if mismatches:
            details = "；".join(mismatches)
            raise DatabaseSchemaError(
                "数据库结构与当前 schema.py 不一致："
                f"{details}。请先运行 uv run melonclaw-db-update；"
                "若当前数据库不属于受支持的升级路径，再按重大变更流程重建。"
            )
