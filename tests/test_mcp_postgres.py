"""隔离 schema 的真实 PostgreSQL 约束/事务测试；不触碰应用数据。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import mcp_servers, mcp_user_preferences, metadata, tenants, users
from melonclaw.repository.mappers import _now
from melonclaw.repository.mcp import McpConflictError
from melonclaw.repository.repository import BusinessRepository
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.mcp_management import McpManagementService


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离验证数据库连接")
def test_mcp_postgres_constraints_and_transactions(monkeypatch):
    async def run():
        schema = "test_mcp_" + uuid4().hex
        engine = create_async_engine(
            normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.run_sync(
                    lambda conn: metadata.create_all(
                        conn, tables=[tenants, users, mcp_servers, mcp_user_preferences]
                    )
                )
                await connection.execute(
                    insert(tenants).values(
                        tenant_id="test", tenant_name_zh="测试", created_at=_now()
                    )
                )
                for user_id, role in [("admin", "admin"), ("alice", "member"), ("bob", "member")]:
                    await connection.execute(
                        insert(users).values(
                            user_id=user_id,
                            tenant_id="test",
                            user_name_zh="测试",
                            tenant_role=role,
                            created_at=_now(),
                        )
                    )
            repository = BusinessRepository(SimpleNamespace(engine=engine))
            manager = McpManagementService(SimpleNamespace(require_ready=lambda: repository, mcp_discovery=McpDiscoveryCoordinator()))
            config = dict(
                slug="demo",
                display_name="演示",
                description="",
                transport="http",
                url="https://example.com/mcp",
                command=None,
                args=[],
                tool_allowlist=None,
            )
            shared = await manager.create("admin", config)
            private = await manager.create("alice", config)
            await manager.create("bob", config)
            with pytest.raises(McpConflictError):
                await manager.create("alice", config)
            await manager.global_state("admin", shared["id"], 1, True)
            await manager.add("alice", private["id"], 1)
            rows = (await manager.list("alice"))["items"]
            assert next(row for row in rows if row["scope"] == "user")["effective_enabled"]
            assert next(row for row in rows if row["scope"] == "global")["shadowed"]
            with pytest.raises(McpConflictError):
                await manager.delete("alice", private["id"], 1)
            await manager.preference("alice", "demo", False)
            await manager.delete("alice", private["id"], 2)
            rows = (await manager.list("alice"))["items"]
            assert len(rows) == 1 and not rows[0]["effective_enabled"]
            await manager.add("alice", shared["id"], 2)
            assert (await manager.list("alice"))["items"][0]["effective_enabled"]
            from melonclaw.repository import bootstrap

            monkeypatch.setattr(
                bootstrap,
                "load_builtin_mcp_seed",
                lambda: {
                    "seed_test": {
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"X-Key": "${MCP_TEST_KEY}"},
                    }
                },
            )
            await bootstrap.seed_builtin_data(SimpleNamespace(engine=engine))
            seed = next(
                item
                for item in (await manager.list("admin"))["items"]
                if item["slug"] == "seed_test"
            )
            assert seed["enabled"] and seed["effective_enabled"]
            await manager.global_state("admin", seed["id"], 1, False)
            await bootstrap.seed_builtin_data(SimpleNamespace(engine=engine))
            seed = next(
                item
                for item in (await manager.list("admin"))["items"]
                if item["slug"] == "seed_test"
            )
            assert not seed["enabled"] and seed["version"] == 2
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    asyncio.run(run())
