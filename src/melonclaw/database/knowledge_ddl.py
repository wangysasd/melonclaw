"""初始化与升级共用的知识库 DDL。"""
import warnings

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.exc import SAWarning

from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.knowledge_schema import SEARCH_VIEW_SQL


async def ensure_vector(connection):
    available = await connection.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='vector')"
    ))
    if not available:
        raise DatabaseSchemaError("PostgreSQL 尚未安装 pgvector；请按 ops/PGVECTOR.md 安装后重试。")
    installed = await connection.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')"
    ))
    if not installed:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


async def verify_knowledge(connection, tables, *, deferred_columns=None, deferred_objects=()):
    """验证列、非空、命名约束和索引，拒绝既存不完整结构。"""
    def validate(sync):
        inspector = inspect(sync)
        for table in tables:
            deferred = (deferred_columns or {}).get(table.name, set())
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message="Did not recognize type 'vector'.*", category=SAWarning)
                columns = {c['name']: c for c in inspector.get_columns(table.name)}
            if set(columns) - deferred != set(table.c.keys()) - deferred:
                raise DatabaseSchemaError(f"{table.name} 列与定义不一致；升级不会重建或删除已有表。")
            for column in table.c:
                if column.name in deferred:
                    continue
                if columns[column.name]['nullable'] != column.nullable:
                    raise DatabaseSchemaError(f"{table.name}.{column.name} 空值约束不一致。")
            actual_checks = {c['name'] for c in inspector.get_check_constraints(table.name)}
            from sqlalchemy import CheckConstraint
            expected_checks = {c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name not in deferred_objects}
            actual_indexes = {i['name'] for i in inspector.get_indexes(table.name)}
            if not expected_checks <= actual_checks or not {i.name for i in table.indexes if i.name not in deferred_objects} <= actual_indexes:
                raise DatabaseSchemaError(f"{table.name} 缺少约束或索引；请检查已有结构。")
            actual_unique = {tuple(c['column_names']) for c in inspector.get_unique_constraints(table.name)}
            expected_unique = {tuple(c.columns.keys()) for c in table.constraints if isinstance(c, UniqueConstraint)}
            if not expected_unique <= actual_unique:
                raise DatabaseSchemaError(f"{table.name} 缺少唯一约束。")
            if tuple(inspector.get_pk_constraint(table.name)['constrained_columns']) != tuple(table.primary_key.columns.keys()):
                raise DatabaseSchemaError(f"{table.name} 主键不一致。")
            actual_fks = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns'])) for f in inspector.get_foreign_keys(table.name)}
            expected_fks = {(tuple(e.parent.name for e in f.elements), f.elements[0].column.table.name, tuple(e.column.name for e in f.elements)) for f in table.foreign_key_constraints if not set(f.columns.keys()) & deferred}
            if not expected_fks <= actual_fks:
                raise DatabaseSchemaError(f"{table.name} 缺少归属外键。")
    await connection.run_sync(validate)
    dimension = await connection.scalar(text("""
        SELECT format_type(a.atttypid,a.atttypmod)
        FROM pg_attribute a WHERE a.attrelid='knowledge_chunks'::regclass AND a.attname='embedding'
    """))
    if dimension != 'vector(1024)':
        raise DatabaseSchemaError("knowledge_chunks.embedding 必须为 vector(1024)。")
    view = await connection.scalar(text("SELECT to_regclass('knowledge_search_chunks')"))
    if view is None:
        raise DatabaseSchemaError("知识库检索视图缺失。")


async def create_knowledge(connection):
    from melonclaw.database.schema import KNOWLEDGE_TABLES, metadata
    await ensure_vector(connection)
    await connection.run_sync(lambda sync: metadata.create_all(sync, tables=KNOWLEDGE_TABLES))
    await connection.execute(text(SEARCH_VIEW_SQL))
    await verify_knowledge(connection, KNOWLEDGE_TABLES)
