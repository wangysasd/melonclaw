"""针对保留历史数据的数据库增量更新测试。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.core.passwords import verify_password
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.update_runner import apply_schema_updates


@pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 PostgreSQL 测试连接",
)
def test_account_update_preserves_existing_rows_and_is_repeatable():
    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = "test_db_update_" + uuid4().hex
        control = create_async_engine(url)
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
        database = SimpleNamespace(engine=engine)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE TABLE tenants ("
                        "tenant_id VARCHAR(64) PRIMARY KEY, "
                        "tenant_name_zh VARCHAR(5) NOT NULL UNIQUE, "
                        "created_at TIMESTAMPTZ NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE users ("
                        "user_id VARCHAR(64) PRIMARY KEY, "
                        "tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id), "
                        "user_name_zh VARCHAR(3) NOT NULL, "
                        "tenant_role VARCHAR(32) NOT NULL DEFAULT 'member', "
                        "tenant_status VARCHAR(16) NOT NULL DEFAULT 'active', "
                        "created_at TIMESTAMPTZ NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO tenants VALUES "
                        "('system', '系统', now()), ('team', '团队', now())"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(user_id, tenant_id, user_name_zh, tenant_role, created_at) "
                        "VALUES ('admin', 'system', '管理员', 'owner', now()), "
                        "('alice', 'team', '小王', 'member', now())"
                    )
                )

            applied = await apply_schema_updates(database)
            assert applied == ("20261006_account_management",)
            assert await apply_schema_updates(database) == ()

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE tenants SET tenant_name_zh = '扩展租户名称' "
                        "WHERE tenant_id = 'team'"
                    )
                )
                await connection.execute(
                    text(
                        "UPDATE users SET user_name_zh = '扩展后的用户名称' "
                        "WHERE user_id = 'alice'"
                    )
                )
            async with engine.connect() as connection:
                tenant_rows = (
                    await connection.execute(
                        text(
                            "SELECT tenant_id, tenant_name_zh, enabled, updated_at "
                            "FROM tenants ORDER BY tenant_id"
                        )
                    )
                ).all()
                users = (
                    await connection.execute(
                        text(
                            "SELECT user_id, user_name_zh, tenant_id, password_hash "
                            "FROM users ORDER BY user_id"
                        )
                    )
                ).all()
                sessions_exists = (
                    await connection.execute(
                        text("SELECT to_regclass('auth_sessions')")
                    )
                ).scalar_one()
                assert len(tenant_rows) == 2
                assert all(row.enabled and row.updated_at for row in tenant_rows)
                assert [(row.user_id, row.tenant_id) for row in users] == [
                    ("admin", "system"),
                    ("alice", "team"),
                ]
                assert len(next(row.tenant_name_zh for row in tenant_rows if row.tenant_id == "team")) > 5
                assert len(next(row.user_name_zh for row in users if row.user_id == "alice")) > 3
                assert all(len(row.user_name_zh) > 0 for row in users)
                assert verify_password(
                    "admin", next(row.password_hash for row in users if row.user_id == "admin")
                )
                assert sessions_exists is not None
                assert await connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM melonclaw_schema_updates "
                        "WHERE update_id = '20261006_account_management'"
                    )
                ) == 1
        finally:
            await engine.dispose()
            async with control.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())
