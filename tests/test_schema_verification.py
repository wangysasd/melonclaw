"""verify_schema 的结构漂移校验：业务表（含资源表）必须与 schema.py 一致。"""

import unittest

from melonclaw.database.constants import BUSINESS_TABLES
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.migrations import SchemaMigrationMixin
from melonclaw.database.schema import metadata

# 资源表曾不在 BUSINESS_TABLES 中，结构漂移无人拦截，
# 直到种子 INSERT 才以裸数据库错误暴露。
RESOURCE_TABLE_COLUMNS = (
    ("skills", "storage_path"),
    ("skill_user_states", "enabled"),
    ("mcp_servers", "tool_allowlist"),
    ("model_configs", "is_default"),
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, tables, columns, attachment_constraint):
        self._tables = tables
        self._columns = columns
        self._attachment_constraint = attachment_constraint

    async def execute(self, query, _values):
        # 按查询文本分派两种 information_schema 查询。
        if "information_schema.tables" in str(query):
            return FakeResult([(name,) for name in self._tables])
        if "pg_get_constraintdef" in str(query):
            return FakeResult([(self._attachment_constraint,)] if self._attachment_constraint else [])
        return FakeResult(list(self._columns))


class FakeConnectionContext:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, *_exc):
        return False


class FakeEngine:
    def __init__(self, tables, columns, attachment_constraint):
        self._connection = FakeConnection(tables, columns, attachment_constraint)

    def connect(self):
        return FakeConnectionContext(self._connection)


class FakeDatabase(SchemaMigrationMixin):
    def __init__(self, tables, columns, attachment_constraint):
        self.engine = FakeEngine(tables, columns, attachment_constraint)


def expected_columns() -> list[tuple[str, str]]:
    return [
        (table_name, column_name)
        for table_name in BUSINESS_TABLES
        for column_name in metadata.tables[table_name].columns.keys()
    ]


def make_database(
    *,
    tables: list[str] | None = None,
    columns: list[tuple[str, str]] | None = None,
    attachment_constraint: str | None = "kind IN ('image', 'archive')",
) -> FakeDatabase:
    return FakeDatabase(
        list(BUSINESS_TABLES) if tables is None else tables,
        expected_columns() if columns is None else columns,
        attachment_constraint,
    )


def without_column(table_name: str, column_name: str) -> list[tuple[str, str]]:
    return [
        item
        for item in expected_columns()
        if item != (table_name, column_name)
    ]


class SchemaVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_consistent_schema_passes(self):
        await make_database().verify_schema(require_checkpointer=False)

    async def test_old_attachment_constraint_is_reported_before_upload(self):
        database = make_database(attachment_constraint="kind IN ('image', 'pdf', 'text', 'document')")
        with self.assertRaises(DatabaseSchemaError) as caught:
            await database.verify_schema(require_checkpointer=False)
        self.assertIn("ZIP 无法上传", str(caught.exception))
        self.assertIn("重建附件两表", str(caught.exception))

    async def test_missing_column_is_reported(self):
        database = make_database(
            columns=without_column("model_configs", "is_default")
        )
        with self.assertRaises(DatabaseSchemaError) as caught:
            await database.verify_schema(require_checkpointer=False)
        message = str(caught.exception)
        assert "model_configs" in message
        assert "is_default" in message
        assert "重建数据库" in message

    async def test_extra_column_is_reported(self):
        database = make_database(
            columns=expected_columns() + [("skills", "legacy_column")]
        )
        with self.assertRaises(DatabaseSchemaError) as caught:
            await database.verify_schema(require_checkpointer=False)
        assert "legacy_column" in str(caught.exception)

    async def test_missing_table_is_reported(self):
        database = make_database(
            tables=[
                name for name in BUSINESS_TABLES if name != "skill_user_states"
            ]
        )
        with self.assertRaises(DatabaseSchemaError) as caught:
            await database.verify_schema(require_checkpointer=False)
        assert "skill_user_states" in str(caught.exception)

    async def test_resource_table_column_drift_is_detected(self):
        """四张资源表各自丢一列都必须被验证捕获。"""

        for table_name, column_name in RESOURCE_TABLE_COLUMNS:
            with self.subTest(table=f"{table_name}.{column_name}"):
                database = make_database(
                    columns=without_column(table_name, column_name)
                )
                with self.assertRaises(DatabaseSchemaError) as caught:
                    await database.verify_schema(require_checkpointer=False)
                message = str(caught.exception)
                assert table_name in message
                assert column_name in message


class BusinessTablesContractTests(unittest.TestCase):
    def test_resource_tables_are_listed(self):
        for table_name, _ in RESOURCE_TABLE_COLUMNS:
            assert table_name in BUSINESS_TABLES, table_name

    def test_every_listed_table_exists_in_schema(self):
        for table_name in BUSINESS_TABLES:
            assert table_name in metadata.tables, table_name


if __name__ == "__main__":
    unittest.main()
