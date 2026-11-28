"""知识库建表与 PostgreSQL 约束回归。"""
import asyncio
import os

import pytest
from sqlalchemy import create_mock_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.database.knowledge_ddl import create_knowledge
from melonclaw.database.schema import KNOWLEDGE_TABLES, metadata


def test_knowledge_update_is_registered_after_environment():
    from melonclaw.database.updates import UPDATES
    from melonclaw.database.updates.knowledge import upgrade
    from melonclaw.database.updates.knowledge_moderation import upgrade as knowledge_moderation
    from melonclaw.database.updates.model_purpose import upgrade as model_purpose
    from melonclaw.database.updates.parser_model import upgrade as parser_model

    ids = [item.update_id for item in UPDATES]
    assert ids.index("20261117_knowledge") == ids.index("20261017_environment") + 1
    assert ids.index("20261117_model_purpose") == ids.index("20261117_knowledge") + 1
    assert ids.index("20261117_parser_model") == ids.index("20261117_model_purpose") + 1
    assert ids.index("20261126_knowledge_moderation") == ids.index("20261117_parser_model") + 1
    assert UPDATES[ids.index("20261117_knowledge")].upgrade is upgrade
    assert UPDATES[ids.index("20261117_model_purpose")].upgrade is model_purpose
    assert UPDATES[ids.index("20261117_parser_model")].upgrade is parser_model
    assert UPDATES[ids.index("20261126_knowledge_moderation")].upgrade is knowledge_moderation


def test_knowledge_ddl_has_fixed_vector_and_cyclic_foreign_key():
    statements = []
    engine = create_mock_engine('postgresql://', lambda sql, *a, **k: statements.append(str(sql.compile(dialect=engine.dialect))))
    metadata.create_all(engine, tables=KNOWLEDGE_TABLES)
    ddl = '\n'.join(statements)
    assert 'VECTOR(1024)' in ddl
    assert "'base_suspended'" in ddl and "'base_restored'" in ddl and "'base_created'" in ddl
    assert 'ALTER TABLE knowledge_documents ADD CONSTRAINT fk_kb_document_active_revision' in ddl
    assert 'GENERATED ALWAYS AS' in ddl
    assert len(KNOWLEDGE_TABLES) == 8
    assert not any("wiki" in table.name for table in metadata.tables.values())


@pytest.mark.skipif(not os.getenv('MELONCLAW_TEST_DATABASE_URL'), reason='需要隔离 PostgreSQL 测试库及 pgvector')
def test_knowledge_postgres_upgrade_and_constraints():
    async def run():
        engine = create_async_engine(os.environ['MELONCLAW_TEST_DATABASE_URL'])
        async with engine.connect() as preflight:
            existing = await preflight.run_sync(lambda sync: inspect(sync).get_table_names())
            if existing:
                await engine.dispose()
                pytest.skip('此测试要求专用空测试数据库，不修改已有表')
        # 此测试仅接受由执行者提供的专用测试库，事务回滚所有业务对象。
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(text('CREATE TABLE users(user_id varchar(64) PRIMARY KEY, tenant_id varchar(64))'))
                await conn.execute(text('CREATE TABLE model_configs(id uuid PRIMARY KEY)'))
                await conn.execute(text("INSERT INTO users VALUES ('owner','test')"))
                await create_knowledge(conn)
                await create_knowledge(conn)
                assert await conn.scalar(text('SELECT count(*) FROM users')) == 1
                await conn.execute(text("INSERT INTO model_configs VALUES ('00000000-0000-0000-0000-000000000001')"))
                await conn.execute(text("""INSERT INTO knowledge_bases
                    (id, owner_user_id,name,embedding_model_id,chunk_config,created_at,updated_at)
                    VALUES ('00000000-0000-0000-0000-000000000002','owner','test',
                    '00000000-0000-0000-0000-000000000001','{}',now(),now())"""))
                async with conn.begin_nested() as nested:
                    with pytest.raises(Exception, match='ck_kb_base_deleted'):
                        await conn.execute(text("UPDATE knowledge_bases SET status='deleted'"))
                    await nested.rollback()
                await conn.execute(text("""INSERT INTO knowledge_documents
                    (id,kb_id,display_name,created_by,created_at,updated_at)
                    VALUES ('00000000-0000-0000-0000-000000000003',
                    '00000000-0000-0000-0000-000000000002','doc','owner',now(),now())"""))
                await conn.execute(text("""INSERT INTO knowledge_document_revisions
                    (id,kb_id,document_id,revision_no,source_key,source_hash,source_byte_size,mime_type,processing_spec,created_at)
                    VALUES ('00000000-0000-0000-0000-000000000004',
                    '00000000-0000-0000-0000-000000000002',
                    '00000000-0000-0000-0000-000000000003',1,'source',repeat('a',64),1,'text/plain','{}',now())"""))
                for invalid, constraint in [
                    ("UPDATE knowledge_document_revisions SET superseded_at=now()", 'ck_kb_superseded_time'),
                    ("UPDATE knowledge_documents SET active_revision_id='00000000-0000-0000-0000-000000000005'", 'fk_kb_document_active_revision'),
                ]:
                    async with conn.begin_nested() as nested:
                        with pytest.raises(Exception, match=constraint):
                            await conn.execute(text(invalid))
                        await nested.rollback()
                assert await conn.scalar(text('SELECT count(*) FROM knowledge_search_chunks')) == 0
            finally:
                await transaction.rollback()
                await engine.dispose()
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv('MELONCLAW_TEST_DATABASE_URL'), reason='需要隔离 PostgreSQL 测试库及 pgvector')
def test_knowledge_init_schema_is_valid():
    from melonclaw.database.knowledge_ddl import ensure_vector, verify_knowledge
    from melonclaw.database.knowledge_schema import SEARCH_VIEW_SQL

    async def run():
        engine = create_async_engine(os.environ['MELONCLAW_TEST_DATABASE_URL'])
        async with engine.connect() as preflight:
            existing = await preflight.run_sync(lambda sync: inspect(sync).get_table_names())
            if existing:
                await engine.dispose()
                pytest.skip('此测试要求专用空测试数据库，不修改已有表')
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await ensure_vector(conn)
                await conn.run_sync(lambda sync: metadata.create_all(sync))
                await conn.execute(text(SEARCH_VIEW_SQL))
                await verify_knowledge(conn, KNOWLEDGE_TABLES)
            finally:
                await tx.rollback()
                await engine.dispose()
    asyncio.run(run())
