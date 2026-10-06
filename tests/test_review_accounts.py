"""针对账户认证和 schema 更新新增边界的回归测试。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.api.app import create_app
from melonclaw.core.passwords import hash_password, verify_password
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.update_runner import apply_schema_updates
from melonclaw.database.updates import SchemaUpdate
from melonclaw.repository import BusinessRepository
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.models import UserContext


class _MemoryAccounts:
    """只替代数据库读写的认证路由测试存储。"""

    def __init__(self, encoded: str):
        self.encoded = encoded
        self.sessions: dict[str, dict[str, str]] = {}
        self.users = {
            user_id: UserContext(user_id, user_id, "system", "系统")
            for user_id in ("admin", "alice")
        }

    @asynccontextmanager
    async def account_guard(self, **_kwargs):
        yield

    async def get_user_context(self, user_id):
        return self.users.get(user_id)

    async def account_password(self, user_id):
        return self.encoded if user_id in self.users else None

    async def create_auth_session(self, token_hash, user_id):
        self.sessions[token_hash] = {"user_id": user_id, "login_user_id": user_id}

    async def get_auth_session(self, token_hash):
        return self.sessions.get(token_hash)


@pytest.mark.parametrize(
    "encoded",
    ["scrypt", "scrypt$salt", "scrypt$salt$hash$extra", "scrypt$salt$hash",
     "scrypt$" + "a" * 32 + "$" + "é" * 128],
)
def test_malformed_password_hash_is_a_normal_authentication_failure(encoded):
    """损坏的持久化散列应返回 False，不能让验证函数抛出解析异常。"""

    assert verify_password("valid-password", encoded) is False


def test_cookie_identity_assertions_reject_header_query_and_json_spoofing(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        storage = _MemoryAccounts(hash_password("password-123"))
        app.state.chat = SimpleNamespace(storage=storage)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login",
                json={"user_id": "alice", "password": "password-123"},
            )
            assert login.status_code == 200

            assert (
                await client.get(
                    "/api/status", headers={"X-Melonclaw-User": "admin"}
                )
            ).status_code == 409
            assert (
                await client.get("/api/projects?user_id=alice&user_id=admin")
            ).status_code == 409
            assert (
                await client.post(
                    "/api/projects", json={"user_id": "admin", "name": "spoof"}
                )
            ).status_code == 409
            assert (
                await client.post(
                    "/api/admin/users",
                    json={
                        "user_id": "bob",
                        "user_name_zh": "用户",
                        "tenant_id": "system",
                        "password": "bob-password",
                        "confirm_password": "bob-password",
                    },
                )
            ).status_code == 403

    asyncio.run(run())


def test_configured_cors_origin_can_submit_login(monkeypatch):
    monkeypatch.setenv("profile", "")
    monkeypatch.setenv("MELONCLAW_ALLOWED_ORIGINS", "http://api.test:8001")

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(
            storage=_MemoryAccounts(hash_password("password-123"))
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"user_id": "alice", "password": "password-123"},
                headers={"Origin": "http://api.test:8001", "Sec-Fetch-Site": "same-site"},
            )
            assert response.status_code == 200

    asyncio.run(run())


@pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 PostgreSQL 测试连接",
)
def test_schema_update_rolls_back_earlier_steps_and_update_records(monkeypatch):
    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = "test_review_update_" + uuid4().hex
        control = create_async_engine(url)
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("CREATE TABLE tenants (tenant_id VARCHAR(64) PRIMARY KEY)")
                )
                await connection.execute(
                    text("CREATE TABLE users (user_id VARCHAR(64) PRIMARY KEY)")
                )
                await connection.execute(text("INSERT INTO users VALUES ('admin')"))

            async def create_probe(connection):
                await connection.execute(
                    text("CREATE TABLE review_rollback_probe (value INTEGER NOT NULL)")
                )

            async def fail_after_probe(connection):
                await connection.execute(
                    text("INSERT INTO review_rollback_probe VALUES (1)")
                )
                raise RuntimeError("injected update failure")

            monkeypatch.setattr(
                "melonclaw.database.update_runner.UPDATES",
                (
                    SchemaUpdate("review_1", "test first step", create_probe),
                    SchemaUpdate("review_2", "test failing step", fail_after_probe),
                ),
            )
            with pytest.raises(RuntimeError, match="injected update failure"):
                await apply_schema_updates(SimpleNamespace(engine=engine))

            async with engine.connect() as connection:
                assert await connection.scalar(
                    text("SELECT to_regclass('review_rollback_probe')")
                ) is None
                assert await connection.scalar(
                    text("SELECT to_regclass('melonclaw_schema_updates')")
                ) is None
                assert await connection.scalar(text("SELECT COUNT(*) FROM users")) == 1
        finally:
            await engine.dispose()
            async with control.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())


@pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 PostgreSQL 测试连接",
)
def test_account_guard_coordinates_distinct_repository_instances():
    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = "test_review_guard_" + uuid4().hex
        control = create_async_engine(url)
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine_a = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
        engine_b = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
        repository_a = BusinessRepository(SimpleNamespace(engine=engine_a))
        repository_b = BusinessRepository(SimpleNamespace(engine=engine_b))
        try:
            async with repository_a.account_guard():
                # 共享业务锁可以并行持有；管理更新的独占锁必须立即冲突。
                async with repository_b.account_guard():
                    pass
                with pytest.raises(AccountConflict):
                    async with repository_b.account_guard(exclusive=True):
                        pass

            async with repository_b.account_guard(exclusive=True):
                pass
        finally:
            await engine_a.dispose()
            await engine_b.dispose()
            async with control.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("ready", [False, True])
def test_public_readiness_matches_manager_without_exposing_errors(ready):
    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(ready=ready, storage=None, startup_error="private error")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/ready")
            assert response.status_code == (200 if ready else 503)
            assert response.json() == {"ready": ready}
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get("/api/auth/config")).status_code == 200
    asyncio.run(run())


@pytest.mark.parametrize("origin,site,status", [
    ("http://api.test", "same-origin", 200),
    ("http://api.test:8001", "same-site", 200),
    ("http://api.test:8002", "same-site", 403),
    ("http://frontend.test", "cross-site", 403),
    ("null", "same-site", 403),
])
def test_login_origin_boundary_and_cors_preflight(monkeypatch, origin, site, status):
    monkeypatch.setenv("profile", "")
    monkeypatch.setenv("MELONCLAW_ALLOWED_ORIGINS", "http://api.test:8001,http://frontend.test")
    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=_MemoryAccounts(hash_password("password-123")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
            preflight = await client.options("/api/auth/login", headers={
                "Origin": "http://api.test:8001", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,X-Melonclaw-User",
            })
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "http://api.test:8001"
            response = await client.post("/api/auth/login", json={
                "user_id": "alice", "password": "password-123",
            }, headers={"Origin": origin, "Sec-Fetch-Site": site})
            assert response.status_code == status
            if status == 200:
                assert "SameSite=strict" in response.headers["set-cookie"]
                assert (await client.get("/api/auth/session")).status_code == 200
    asyncio.run(run())


def test_corrupt_stored_password_returns_uniform_login_failure():
    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=_MemoryAccounts("scrypt$broken"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/auth/login", json={"user_id": "alice", "password": "valid-password"})
            assert response.status_code == 401
            assert response.json() == {"detail": "用户 ID 或密码错误。"}
            assert "set-cookie" not in response.headers
    asyncio.run(run())
