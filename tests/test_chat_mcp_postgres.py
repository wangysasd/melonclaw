"""真实 PostgreSQL 验证聊天安装原子性、并发幂等与个人归属。"""

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import mcp_install_drafts, mcp_servers, metadata, tenants, users
from melonclaw.repository.mappers import _now
from melonclaw.repository.repository import BusinessRepository
from melonclaw.services.mcp_chat_config import parse_chat_mcp
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.mcp_install import ChatMcpInstallService


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离验证数据库连接")
def test_chat_mcp_install_transaction_and_race():
    async def run():
        schema = "test_chat_mcp_" + uuid4().hex
        engine = create_async_engine(normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
                                     connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.run_sync(metadata.create_all)
                await connection.execute(insert(tenants).values(tenant_id="test", tenant_name_zh="测试", created_at=_now()))
                await connection.execute(insert(users).values(user_id="admin", tenant_id="test", user_name_zh="管理员", tenant_role="admin", created_at=_now()))
            storage = BusinessRepository(SimpleNamespace(engine=engine))
            conversation = await storage.create_conversation("admin", None)
            cid = conversation["id"]
            context = SimpleNamespace(user_id="admin", tenant_id="test", conversation_id=cid, project_id="")
            chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: storage, mcp_discovery=McpDiscoveryCoordinator()),
                                   conversations=SimpleNamespace(resolve_user=storage.get_user_context))
            provider = ChatMcpInstallService(chat)
            _, items = parse_chat_mcp(json.dumps({"mcpServers": {"demo": {"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer fixture-only"}}}}),
                                      user_id="admin", conversation_id=str(cid), request_id="req")
            await storage.stage_mcp_drafts("admin", cid, items)
            preview = await provider.prepare(context, items[0]["id"], True, [])
            assert preview["status"] == "prepared"
            preview2 = await provider.prepare(context, items[0]["id"], False, None)
            assert preview2["status"] == "prepared"
            results = await asyncio.gather(provider.confirm(context, preview["installation"]), provider.confirm(context, preview["installation"]))
            assert all(result["status"] == "installed" for result in results)
            assert results[0]["id"] == results[1]["id"]
            rows = await storage.list_visible_mcp_rows("admin")
            assert len(rows) == 1 and rows[0]["scope"] == "user" and rows[0]["owner_user_id"] == "admin"
            assert rows[0]["enabled"] and rows[0]["user_enabled"] is True and rows[0]["tool_allowlist"] == []
            async with engine.connect() as connection:
                draft = (await connection.execute(select(mcp_install_drafts).where(mcp_install_drafts.c.installed_id.is_not(None)))).mappings().one()
                assert draft["payload"] == {}
                assert len((await connection.execute(select(mcp_servers))).all()) == 1
            # 两份独立审批清单抢同一 slug：第二份失败，不能留下额外偏好或结果。
            assert (await provider.confirm(context, preview2["installation"]))["status"] == "error"
            assert (await storage.list_visible_mcp_rows("admin"))[0]["user_enabled"] is True
            pending = await storage.get_mcp_install_draft(preview2["installation"]["draft_id"], "admin", cid)
            assert pending["installed_id"] is None
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()
    asyncio.run(run())
