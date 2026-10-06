"""统一身份边界与真实认证路由；存储替身只替代 SQL。"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from melonclaw.api.app import create_app
from melonclaw.core.passwords import hash_password
from melonclaw.repository.models import UserContext


class MemoryAccounts:
    def __init__(self):
        self.sessions = {}
        self.users = {name: UserContext(name, name, "system", "系统") for name in ["admin", "alice"]}
        self.encoded = hash_password("password-123")

    @asynccontextmanager
    async def account_guard(self, **kwargs):
        yield

    async def get_user_context(self, user_id):
        return self.users.get(user_id)

    async def account_password(self, user_id):
        return self.encoded if user_id in self.users else None

    async def create_auth_session(self, token, user_id):
        self.sessions[token] = {"user_id": user_id, "login_user_id": user_id}

    async def get_auth_session(self, token):
        return self.sessions.get(token)

    async def delete_auth_session(self, token):
        self.sessions.pop(token, None)

    async def switch_auth_session(self, token, user_id):
        self.sessions[token]["user_id"] = user_id

    async def set_account_password(self, user_id, encoded):
        self.encoded = encoded
        self.sessions = {key: value for key, value in self.sessions.items()
                         if user_id not in (value["user_id"], value["login_user_id"])}

    async def account_users(self):
        return []


@pytest.mark.parametrize("profile,enabled", [(None, False), ("", False), ("DEV", False), ("prod", False), ("dev", True)])
def test_profile_gate_and_no_password_echo(monkeypatch, profile, enabled):
    if profile is None:
        monkeypatch.delenv("profile", raising=False)
    else:
        monkeypatch.setenv("profile", profile)
    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/auth/config")).json() == {"passwordless": enabled}
            response = await client.post("/api/auth/passwordless")
            assert response.status_code == (200 if enabled else 403)
            await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            assert (await client.post("/api/auth/switch", json={"user_id": "admin"})).status_code == (200 if enabled else 403)
            response = await client.post("/api/auth/login", json={"user_id": "alice", "password": "tiny"})
            assert response.status_code == 422 and "tiny" not in response.text
    asyncio.run(run())


def test_login_switch_admin_logout_and_all_route_guards(monkeypatch):
    monkeypatch.setenv("profile", "dev")
    async def run():
        app = create_app()
        storage = MemoryAccounts()
        app.state.chat = SimpleNamespace(storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for path in ["/api/status", "/api/dev/users", "/api/projects", "/api/admin/users", "/api/conversations/00000000-0000-0000-0000-000000000001/files/content"]:
                assert (await client.get(path)).status_code == 401
            body = {"user_id": "alice", "password": "password-123"}
            assert (await client.post("/api/auth/login", json={**body, "password": "incorrect"})).status_code == 401
            response = await client.post("/api/auth/login", json=body)
            assert response.status_code == 200
            assert "HttpOnly" in response.headers["set-cookie"]
            assert "SameSite=strict" in response.headers["set-cookie"]
            assert (await client.get("/api/auth/session")).json()["user_id"] == "alice"
            assert (await client.get("/api/admin/users")).status_code == 403
            assert (await client.get("/api/projects?user_id=admin")).status_code == 409
            assert (await client.post("/api/auth/switch", json={"user_id": "admin"}, headers={"Origin": "http://evil.test"})).status_code == 403
            assert (await client.post("/api/auth/switch", json={"user_id": "admin"})).status_code == 200
            assert (await client.get("/api/admin/users")).status_code == 200
            assert (await client.get("/api/auth/session")).json()["user_id"] == "admin"
            old_cookie = client.cookies.get("melonclaw_session")
            assert (await client.post("/api/auth/logout")).status_code == 200
            client.cookies.set("melonclaw_session", old_cookie)
            assert (await client.get("/api/auth/session")).status_code == 401
    asyncio.run(run())


def test_self_password_confirmation_scope_and_session_revocation(monkeypatch):
    monkeypatch.setenv("profile", "")
    async def run():
        app = create_app()
        storage = MemoryAccounts()
        app.state.chat = SimpleNamespace(storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"password": "new-password", "confirm_password": "new-password"}
            assert (await client.post("/api/auth/password", json=body)).status_code == 401
            await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            original = storage.encoded
            response = await client.post("/api/auth/password", json={**body, "confirm_password": "different"})
            assert response.status_code == 422
            assert "new-password" not in response.text
            assert storage.encoded == original
            assert (await client.post("/api/auth/password", json={**body, "user_id": "admin"})).status_code == 422
            assert (await client.post("/api/admin/users/admin/password", json=body)).status_code == 403
            assert (await client.post("/api/auth/password", json=body)).status_code == 200
            assert not storage.sessions
            assert (await client.get("/api/auth/session")).status_code == 401
            assert (await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})).status_code == 401
            assert (await client.post("/api/auth/login", json={"user_id": "alice", "password": "new-password"})).status_code == 200
    asyncio.run(run())
