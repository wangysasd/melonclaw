"""可选真实 PostgreSQL 集成测试，只创建并清理独立临时 schema。"""
import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.api.app import create_app
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import (
    chat_conversations,
    chat_messages,
    metadata,
    projects,
    users,
)
from melonclaw.memory.service import namespace_for_context
from melonclaw.repository import BusinessRepository
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.bootstrap import seed_demo_data
from melonclaw.repository.mappers import _now
from melonclaw.services.accounts import AccountService


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")
def test_real_account_lifecycle(monkeypatch):
    monkeypatch.setenv("profile", "dev")
    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = "test_accounts_" + uuid4().hex
        control = create_async_engine(url)
        async with control.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            database = SimpleNamespace(engine=engine)
            await seed_demo_data(database)
            repo = BusinessRepository(database)
            service = AccountService(repo)
            app = create_app()
            app.state.chat = SimpleNamespace(storage=repo)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.post("/api/auth/login", json={"user_id": "admin", "password": "admin"})).status_code == 200
                await service.reset_password("admin", "changed-admin-password")
                await seed_demo_data(database)
                assert (await client.post("/api/auth/login", json={"user_id": "admin", "password": "admin"})).status_code == 401
                assert (await client.post("/api/auth/login", json={"user_id": "admin", "password": "changed-admin-password"})).status_code == 200
                async def post(path, body):
                    response = await client.post(path, json=body)
                    assert response.status_code in {200, 201}, response.text
                    return response
                await post("/api/admin/tenants", {"tenant_id": "team", "tenant_name_zh": "新租户"})
                await post("/api/admin/users", {"user_id": "alice", "user_name_zh": "测试完整用户名", "tenant_id": "team", "password": "initial-password", "confirm_password": "initial-password"})
                assert (await client.post("/api/admin/users", json={"user_id": "alice", "user_name_zh": "重复", "tenant_id": "team", "password": "initial-password", "confirm_password": "initial-password"})).status_code == 422
                assert "password_hash" not in (await client.get("/api/admin/users")).text
                pid, cid = uuid4(), uuid4()
                async with engine.begin() as conn:
                    await conn.execute(insert(projects).values(id=pid, user_id="alice", name="个人项目", workdir_path="test", created_at=_now(), updated_at=_now()))
                    await conn.execute(insert(chat_conversations).values(id=cid, user_id="alice", project_id=pid, title="会话", agent_id="test", created_at=_now(), updated_at=_now()))
                # 归属修改被正在准备的业务请求共享锁阻止。
                async with repo.account_guard():
                    with pytest.raises(AccountConflict):
                        await service.save_user("alice", "用户", "system")
                before = await repo.get_user_context("alice")
                response = await client.patch("/api/admin/users/alice", json={"user_name_zh": "用户", "tenant_id": "system"})
                assert response.status_code == 200, response.text
                after = await repo.get_user_context("alice")
                assert after.tenant_id == "system"
                assert namespace_for_context(before, "user") == namespace_for_context(after, "user")
                assert namespace_for_context(before, "tenant") != namespace_for_context(after, "tenant")
                async with engine.connect() as conn:
                    assert (await conn.execute(select(projects.c.user_id).where(projects.c.id == pid))).scalar() == "alice"
                token = await service.login("alice", "initial-password")
                await service.switch(token, "admin")
                # 重置原登录者密码也撤销已切换到其他用户的会话，但不赋予原身份权限。
                await post("/api/admin/users/alice/password", {"password": "changed-password", "confirm_password": "changed-password"})
                assert await service.session(token) is None
                with pytest.raises(ValueError):
                    await service.login("alice", "initial-password")
                token = await service.login("alice", "changed-password")
                # 已挂起的任务阻止变更与删除。
                mid = uuid4()
                async with engine.begin() as conn:
                    await conn.execute(insert(chat_messages).values(id=mid, conversation_id=cid, seq=1, request_id=str(uuid4()), role="assistant", status="interrupted", created_at=_now(), updated_at=_now()))
                response = await client.delete("/api/admin/users/alice")
                assert response.status_code == 409, response.text
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE chat_messages SET status='completed' WHERE id=:id"), {"id": mid})
                assert (await client.delete("/api/admin/users/alice")).status_code == 200
                assert await service.session(token) is None
                assert await repo.get_user_context("alice") is None
                async with engine.connect() as conn:
                    assert (await conn.execute(select(users.c.tenant_status).where(users.c.user_id == "alice"))).scalar() == "deleted"
                    assert (await conn.execute(select(projects.c.id).where(projects.c.id == pid))).scalar() == pid
                await seed_demo_data(database)
                assert await repo.get_user_context("alice") is None
                assert (await client.delete("/api/admin/users/admin")).status_code == 422
                assert (await client.patch("/api/admin/tenants/system", json={"tenant_name_zh": "系统", "enabled": False})).status_code == 422
                assert (await client.delete("/api/admin/tenants/team")).status_code == 405
                await post("/api/admin/users", {"user_id": "bob", "user_name_zh": "Bob", "tenant_id": "team", "password": "bob-password", "confirm_password": "bob-password"})
                bob = await service.login("bob", "bob-password")
                assert (await client.patch("/api/admin/tenants/team", json={"tenant_name_zh": "新租户", "enabled": False})).status_code == 200
                assert await service.session(bob) is None
                assert (await client.post("/api/auth/switch", json={"user_id": "bob"})).status_code == 422
                with pytest.raises(ValueError):
                    await service.login("bob", "bob-password")
                # 会话过期在数据库中生效。
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE auth_sessions SET expires_at=created_at"))
                assert (await client.get("/api/auth/session")).status_code == 401
        finally:
            await engine.dispose()
            async with control.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()
    asyncio.run(run())
