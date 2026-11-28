"""统一身份边界与真实认证路由；存储替身只替代 SQL。"""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from account_fixtures import use_access_token

from melonclaw.api.app import create_app
from melonclaw.core.passwords import hash_password
from melonclaw.repository.models import UserContext


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class MemoryAccounts:
    def __init__(self):
        self.users = {name: UserContext(name, name, "system", "系统") for name in ["admin", "alice"]}
        self.encoded = hash_password("password-123")

    @asynccontextmanager
    async def account_guard(self, **kwargs):
        yield

    async def get_user_context(self, user_id):
        return self.users.get(user_id)

    async def active_user(self, user_id):
        return self.users.get(user_id)

    async def account_password(self, user_id):
        return self.encoded if user_id in self.users else None

    async def set_account_password(self, user_id, encoded):
        if user_id not in self.users:
            raise ValueError("用户不存在。")
        self.encoded = encoded

    async def set_account_name(self, user_id, user_name_zh):
        if user_id not in self.users:
            raise ValueError("用户不存在。")
        self.users[user_id] = replace(self.users[user_id], user_name_zh=user_name_zh)

    async def account_users(self, *, include_deleted_registrations=False):
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
            monkeypatch.setenv("MELONCLAW_NAME", " Melon ")
            monkeypatch.delenv("MELONCLAW_OPERATOR_NAME", raising=False)
            monkeypatch.delenv("MELONCLAW_REPORT_CONTACT_EMAIL", raising=False)
            assert (await client.get("/api/auth/config")).json() == {
                "passwordless": enabled, "assistant_name": "Melon", "registration": False, "sms_login": False,
                "operator_name": "", "operator_email": "",
            }
            response = await client.post("/api/auth/passwordless")
            assert response.status_code == (200 if enabled else 403)
            if enabled:
                assert response.json()["access_token"]
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, logged)
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
            paths = ["/api/status", "/api/dev/users", "/api/projects", "/api/admin/users", "/api/conversations/00000000-0000-0000-0000-000000000001/files/content"]
            first = await client.get(paths[0])
            assert first.status_code == 401
            cookie = first.headers.get("set-cookie", "")
            assert cookie.startswith("melonclaw_device=")
            assert "access_token" not in cookie
            for path in paths[1:]:
                assert (await client.get(path)).status_code == 401
            body = {"user_id": "alice", "password": "password-123"}
            assert (await client.post("/api/auth/login", json={**body, "password": "incorrect"})).status_code == 401
            response = await client.post("/api/auth/login", json=body)
            assert response.status_code == 200
            alice = use_access_token(client, response)
            assert "access_token" not in response.headers.get("set-cookie", "")
            assert (await client.get("/api/auth/session")).json()["user_id"] == "alice"
            assert (await client.get("/api/admin/users")).status_code == 403
            assert (await client.get("/api/projects?user_id=admin")).status_code == 409
            assert (await client.post("/api/auth/switch", json={"user_id": "admin"}, headers={"Origin": "http://evil.test"})).status_code == 403
            switched = await client.post("/api/auth/switch", json={"user_id": "admin"})
            assert switched.status_code == 200
            assert (await client.get("/api/auth/session", headers={"Authorization": f"Bearer {alice}"})).json()["user_id"] == "alice"
            use_access_token(client, switched)
            assert (await client.get("/api/admin/users")).status_code == 200
            assert (await client.get("/api/auth/session")).json()["user_id"] == "admin"
            assert (await client.post("/api/auth/logout")).status_code == 200
            assert (await client.get("/api/auth/session")).json()["user_id"] == "admin"
            del client.headers["Authorization"]
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
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, logged)
            original = storage.encoded
            response = await client.post("/api/auth/password", json={**body, "confirm_password": "different"})
            assert response.status_code == 422
            assert "new-password" not in response.text
            assert storage.encoded == original
            assert (await client.post("/api/auth/password", json={**body, "user_id": "admin"})).status_code == 422
            assert (await client.post("/api/admin/users/admin/password", json=body)).status_code == 403
            assert (await client.post("/api/auth/password", json=body)).status_code == 200
            assert (await client.get("/api/auth/session")).json()["user_id"] == "alice"
            assert (await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})).status_code == 401
            assert (await client.post("/api/auth/login", json={"user_id": "alice", "password": "new-password"})).status_code == 200
    asyncio.run(run())


@pytest.mark.parametrize("name,expected", [(None, "MelonClaw"), ("  ", "MelonClaw"), (" Melon ", "Melon")])
def test_assistant_name_from_backend_environment(monkeypatch, name, expected):
    if name is None:
        monkeypatch.delenv("MELONCLAW_NAME", raising=False)
    else:
        monkeypatch.setenv("MELONCLAW_NAME", name)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app()), base_url="http://test") as client:
            response = await client.get("/api/auth/config")
            assert response.status_code == 200
            assert response.json()["assistant_name"] == expected

    asyncio.run(run())


class CountingAccounts(MemoryAccounts):
    def __init__(self):
        super().__init__()
        self.lookups = 0

    async def active_user(self, user_id):
        self.lookups += 1
        return await super().active_user(user_id)


def test_expired_and_tampered_tokens_skip_the_user_lookup(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        storage = CountingAccounts()
        app.state.chat = SimpleNamespace(storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            token = logged.json()["access_token"]
            use_access_token(client, logged)
            storage.lookups = 0
            header, payload, signature = token.split(".")
            signature = ("A" if signature[:1] != "A" else "B") + signature[1:]
            tampered = await client.get("/api/auth/session", headers={"Authorization": f"Bearer {header}.{payload}.{signature}"})
            assert tampered.status_code == 401
            assert storage.lookups == 0
            del storage.users["alice"]
            monkeypatch.setattr("melonclaw.core.auth_token.time.time", lambda: 10**12)
            expired = await client.get("/api/auth/session")
            assert expired.status_code == 401
            assert storage.lookups == 0

    asyncio.run(run())


def test_deleted_user_is_rejected_on_the_next_request(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        storage = MemoryAccounts()
        app.state.chat = SimpleNamespace(storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, logged)
            assert (await client.get("/api/auth/session")).json()["user_id"] == "alice"
            del storage.users["alice"]
            assert (await client.get("/api/auth/session")).status_code == 401

    asyncio.run(run())


def test_disabled_tenant_lookup_does_not_reject_the_token(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        storage = MemoryAccounts()
        app.state.chat = SimpleNamespace(
            storage=storage, ready=True, status=AsyncMock(return_value={"ready": True}),
            prepare_message=AsyncMock(),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, logged)
            assert (await client.get("/api/status")).status_code == 200

            storage.users["alice"] = replace(storage.users["alice"], tenant_enabled=False)
            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["user_id"] == "alice"
            assert "tenant_enabled" not in session.json()
            assert (await client.get("/api/status")).status_code == 403
            chat = await client.post(f"/api/conversations/{uuid4()}/messages", json={
                "user_id": "alice", "request_id": str(uuid4()), "content": "test",
            })
            assert chat.status_code == 403
            app.state.chat.prepare_message.assert_not_awaited()
            speech = await client.post("/api/speech/transcribe", files={"file": ("test.wav", b"test")})
            assert speech.status_code == 403

            storage.users["alice"] = replace(storage.users["alice"], tenant_enabled=True)
            assert (await client.get("/api/status")).status_code == 200

    asyncio.run(run())


def test_dev_passwordless_token_subject_is_admin(monkeypatch):
    async def issue(profile):
        monkeypatch.setenv("profile", profile)
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/api/auth/passwordless")

    async def run():
        issued = await issue("dev")
        assert issued.status_code == 200
        claims = _claims(issued.json()["access_token"])
        assert claims["sub"] == "admin"
        assert set(claims) == {"sub", "exp"}
        assert isinstance(claims["exp"], int)
        refused = await issue("prod")
        assert refused.status_code == 403
        assert "access_token" not in refused.json()

    asyncio.run(run())


def test_switch_reissues_expiry_and_failure_keeps_the_previous_token(monkeypatch):
    monkeypatch.setenv("profile", "dev")
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "4")
    clock = {"now": 1_800_000_000}
    monkeypatch.setattr("melonclaw.core.auth_token.time.time", lambda: clock["now"])

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            old = logged.json()["access_token"]
            use_access_token(client, logged)
            failed = await client.post("/api/auth/switch", json={"user_id": "missing"})
            assert failed.status_code == 422
            assert "access_token" not in failed.json()
            assert (await client.get("/api/auth/session")).json()["user_id"] == "alice"
            clock["now"] += 3600
            monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "9")
            switched = await client.post("/api/auth/switch", json={"user_id": "admin"})
            assert switched.status_code == 200
            new = switched.json()["access_token"]
            assert _claims(old) == {"sub": "alice", "exp": 1_800_000_000 + 4 * 3600}
            assert _claims(new) == {"sub": "admin", "exp": clock["now"] + 9 * 3600}
            assert (await client.get("/api/auth/session", headers={"Authorization": f"Bearer {old}"})).json()["user_id"] == "alice"
            assert (await client.get("/api/auth/session", headers={"Authorization": f"Bearer {new}"})).json()["user_id"] == "admin"

    asyncio.run(run())


def test_self_profile_nickname_update_scope_and_validation(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        from melonclaw.api.audit import _OPERATIONS
        from melonclaw.repository.mappers import _user_dict

        assert _OPERATIONS["PATCH /api/auth/profile"] == "修改昵称"
        mapped = _user_dict({
            "user_id": "alice", "user_name_zh": "小明", "tenant_id": "u1",
            "tenant_name_zh": "用户alice", "tenant_role": "member", "tenant_status": "active",
        })
        assert mapped["display_name"] == "小明"
        app = create_app()
        storage = MemoryAccounts()
        app.state.chat = SimpleNamespace(storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.patch("/api/auth/profile", json={"user_name_zh": "小明"})).status_code == 401
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, logged)
            assert (await client.patch("/api/auth/profile", json={"user_name_zh": "   "})).status_code == 422
            assert (await client.patch("/api/auth/profile", json={"user_name_zh": "a" * 65})).status_code == 422
            assert (await client.patch("/api/auth/profile", json={"user_name_zh": "小明", "user_id": "admin"})).status_code == 422
            assert (await client.patch("/api/auth/profile", json={"user_name_zh": "小明", "tenant_id": "other"})).status_code == 422
            assert (await client.patch(
                "/api/auth/profile", json={"user_name_zh": "小明"}, headers={"Origin": "http://evil.test"},
            )).status_code == 403
            updated = await client.patch("/api/auth/profile", json={"user_name_zh": "  小明  "})
            assert updated.status_code == 200
            assert updated.json() == {"ok": True, "user_name_zh": "小明"}
            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["user_id"] == "alice"
            assert session.json()["user_name_zh"] == "小明"
            assert storage.users["admin"].user_name_zh == "admin"

    asyncio.run(run())
