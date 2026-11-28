"""短信注册、登录和绑号。供应商与数据库均为替身。"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from account_fixtures import use_access_token
from sms_fakes import FakeSms, MemoryPhoneStore, sms_settings
from test_account_api import MemoryAccounts, _claims

from melonclaw.api.app import create_app
from melonclaw.core.config import load_settings
from melonclaw.core.passwords import hash_password
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.models import UserContext
from melonclaw.services.accounts import AccountService
from melonclaw.services.sms_errors import SmsRequestError
from melonclaw.services.sms_provider import SmsCallResult


class Clock:
    def __init__(self):
        self.current = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


def build(store=None, sms=None, clock=None, **settings):
    return AccountService(
        store or MemoryPhoneStore(), sms=sms or FakeSms(),
        settings=sms_settings(**settings), clock=clock or Clock(),
    )


def test_sms_config_defaults_and_rejects_incomplete_enablement(monkeypatch):
    monkeypatch.delenv("MELONCLAW_SMS_ENABLED", raising=False)
    monkeypatch.delenv("MELONCLAW_REGISTRATION_ENABLED", raising=False)
    assert load_settings().sms_enabled is False
    assert load_settings().registration_enabled is False
    monkeypatch.setenv("MELONCLAW_REGISTRATION_ENABLED", "true")
    with pytest.raises(RuntimeError, match="MELONCLAW_SMS_ENABLED"):
        load_settings()
    monkeypatch.setenv("MELONCLAW_SMS_ENABLED", "true")
    monkeypatch.setenv("MELONCLAW_REGISTRATION_ENABLED", "false")
    with pytest.raises(RuntimeError, match="缺少配置"):
        load_settings()
    for name, value in {
        "MELONCLAW_SMS_ACCESS_KEY_ID": "key-id",
        "MELONCLAW_SMS_ACCESS_KEY_SECRET": "key-secret-value",
        "MELONCLAW_SMS_SIGN_NAME": "赠送签名",
        "MELONCLAW_SMS_TEMPLATE_AUTH": "100001",
        "MELONCLAW_SMS_TEMPLATE_BIND": "100002",
        "MELONCLAW_PHONE_HMAC_KEY": "too-short",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="MELONCLAW_PHONE_HMAC_KEY"):
        load_settings()
    monkeypatch.setenv("MELONCLAW_PHONE_HMAC_KEY", "h" * 32)
    monkeypatch.delenv("MELONCLAW_SMS_TEMPLATE_RESET", raising=False)
    settings = load_settings()
    assert settings.sms_enabled is True
    assert settings.registration_enabled is False
    assert settings.sms_template_reset == "100001"
    monkeypatch.setenv("MELONCLAW_SMS_TEMPLATE_RESET", "100003")
    assert load_settings().sms_template_reset == "100003"
    assert "key-secret-value" not in repr(settings)
    assert settings.sms_phone_interval_seconds == 60
    assert settings.sms_phone_daily_limit == 10


def test_register_then_both_login_paths_share_one_user():
    async def run():
        clock = Clock()
        sms = FakeSms()
        store = MemoryPhoneStore()
        service = build(store, sms, clock)
        sent = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        token = await service.register_with_sms(
            "alice", "password-123", "password-123", "+86 138-0013-8000",
            sent["challenge_id"], "123456",
        )
        assert _claims(token)["sub"] == "alice"
        assert set(_claims(token)) == {"sub", "exp"}
        assert len(store.tenants) == 1
        assert store.phones["alice"][1] == "8000"
        assert sms.sent[0].return_verify_code is False
        assert sms.sent[0].scheme_name == "prod-register"
        assert sms.checks[0].scheme_name == "prod-register"
        assert sms.checks[0].out_id == sms.sent[0].out_id
        assert "##code##" in sms.sent[0].template_param
        record = dict(store.verifications["alice"])
        assert record["verification_id"] == sent["challenge_id"]
        assert record["phone_tail"] == "8000"
        assert "13800138000" not in str(record)
        assert await service.login("alice", "password-123")
        clock.advance(61)
        sms.check_result = SmsCallResult("pass")
        login_sent = await service.send_sms_challenge("13800138000", "login", "203.0.113.10")
        sms_token, user_id = await service.login_with_sms("13800138000", login_sent["challenge_id"], "654321")
        assert user_id == "alice"
        assert _claims(sms_token)["sub"] == "alice"
        assert sms.sent[-1].scheme_name == "prod-login"
        assert sms.sent[-1].template_code == "100001"
        assert store.verifications["alice"] == record

    asyncio.run(run())


def test_unregistered_phone_wrong_code_timeout_unknown_and_cross_purpose():
    async def run():
        clock = Clock()
        sms = FakeSms()
        service = build(sms=sms, clock=clock)
        sent = await service.send_sms_challenge("13800138000", "login", "203.0.113.10")
        with pytest.raises(SmsRequestError, match="尚未注册"):
            await service.login_with_sms("13800138000", sent["challenge_id"], "123456")
        clock.advance(61)
        sent = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        sms.check_result = SmsCallResult("unknown")
        with pytest.raises(SmsRequestError, match="验证码不正确"):
            await service.register_with_sms(
                "alice", "password-123", "password-123", "13800138000", sent["challenge_id"], "000000",
            )
        assert "alice" not in service.storage.users
        assert service.storage.verifications == {}
        sms.check_result = SmsCallResult("pass")
        token = await service.register_with_sms(
            "alice", "password-123", "password-123", "13800138000", sent["challenge_id"], "123456",
        )
        assert _claims(token)["sub"] == "alice"
        clock.advance(61)
        login_sent = await service.send_sms_challenge("13800138000", "login", "203.0.113.10")
        checks_before_cross_purpose = len(sms.checks)
        with pytest.raises(SmsRequestError, match="无效或已过期"):
            await service.login_with_sms("13800138000", sent["challenge_id"], "123456")
        assert len(sms.checks) == checks_before_cross_purpose
        sms.check_result = SmsCallResult("incomplete")
        with pytest.raises(SmsRequestError, match="不确定"):
            await service.login_with_sms("13800138000", login_sent["challenge_id"], "123456")
        assert service.storage.challenges[login_sent["challenge_id"]]["status"] == "uncertain"
        with pytest.raises(SmsRequestError, match="无效或已过期"):
            await service.login_with_sms("13800138000", login_sent["challenge_id"], "123456")

    asyncio.run(run())


def test_send_timeout_vendor_error_resend_and_rate_limits():
    async def run():
        clock = Clock()
        sms = FakeSms()
        sms.send_result = SmsCallResult("timeout")
        service = build(sms=sms, clock=clock)
        with pytest.raises(SmsRequestError, match="超时") as caught:
            await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        assert caught.value.status == 503
        assert len(sms.sent) == 1
        assert service.storage.challenges[list(service.storage.challenges)[0]]["status"] == "uncertain"
        clock.advance(61)
        sms.send_result = SmsCallResult("error", "isv.BUSINESS_LIMIT_CONTROL")
        with pytest.raises(SmsRequestError, match="频繁") as limited:
            await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        assert limited.value.status == 429
        clock.advance(61)
        sms.send_result = SmsCallResult("sent")
        first = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        with pytest.raises(SmsRequestError, match="频繁"):
            await service.send_sms_challenge("13800138000", "login", "203.0.113.10")
        assert len(sms.sent) == 3
        clock.advance(61)
        second = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        assert service.storage.challenges[first["challenge_id"]]["status"] == "replaced"
        with pytest.raises(SmsRequestError, match="无效或已过期"):
            await service.register_with_sms(
                "alice", "password-123", "password-123", "13800138000", first["challenge_id"], "123456",
            )
        await service.register_with_sms(
            "alice", "password-123", "password-123", "13800138000", second["challenge_id"], "123456",
        )

        daily = build(clock=Clock(), sms_phone_daily_limit=1)
        await daily.send_sms_challenge("13700137000", "login", "203.0.113.20")
        daily.clock.advance(61)
        with pytest.raises(SmsRequestError, match="频繁"):
            await daily.send_sms_challenge("13700137000", "login", "203.0.113.20")
        shared_ip = build(clock=Clock(), sms_ip_daily_limit=1)
        await shared_ip.send_sms_challenge("13700137001", "login", "198.51.100.8")
        shared_ip.clock.advance(61)
        with pytest.raises(SmsRequestError, match="频繁"):
            await shared_ip.send_sms_challenge("13700137002", "login", "198.51.100.8")
        global_limit = build(clock=Clock(), sms_global_daily_limit=1)
        await global_limit.send_sms_challenge("13700137003", "login", "203.0.113.30")
        global_limit.clock.advance(61)
        with pytest.raises(SmsRequestError, match="频繁"):
            await global_limit.send_sms_challenge("13700137004", "login", "203.0.113.31")

    asyncio.run(run())


def test_concurrent_submit_creates_one_account_and_duplicate_phone_cannot_bind():
    async def run():
        clock = Clock()
        sms = FakeSms()
        store = MemoryPhoneStore()
        service = build(store, sms, clock)
        sent = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_check(request):
            sms.checks.append(request)
            started.set()
            await release.wait()
            return SmsCallResult("pass")

        sms.check = slow_check

        async def submit(user_id):
            try:
                token = await service.register_with_sms(
                    user_id, "password-123", "password-123", "13800138000",
                    sent["challenge_id"], "123456",
                )
                return "ok", token
            except SmsRequestError as exc:
                return "err", str(exc)

        first = asyncio.create_task(submit("alice"))
        await started.wait()
        second = asyncio.create_task(submit("bob"))
        await asyncio.sleep(0.01)
        release.set()
        results = await asyncio.gather(first, second)
        assert sorted(item[0] for item in results) == ["err", "ok"]
        assert list(store.users) == ["alice"]
        assert list(store.verifications) == ["alice"]
        assert len(store.tenants) == 1
        assert len(sms.checks) == 1

        store.users["bob"] = UserContext("bob", "bob", "team", "团队")
        store.passwords["bob"] = hash_password("password-123")
        clock.advance(61)
        bind_sent = await service.send_sms_challenge("13800138000", "bind", "203.0.113.10", "bob")
        sms.check = FakeSms.check.__get__(sms, FakeSms)
        with pytest.raises(SmsRequestError, match="已绑定其他账号"):
            await service.bind_phone("bob", "password-123", "13800138000", bind_sent["challenge_id"], "123456")
        assert "bob" not in store.phones
        assert store.hmac_owner[store.phones["alice"][0]] == "alice"

    asyncio.run(run())


def test_existing_user_binds_once_and_deleted_or_disabled_tenant_follow_current_rules():
    async def run():
        clock = Clock()
        sms = FakeSms()
        store = MemoryPhoneStore()
        service = build(store, sms, clock)
        store.users["alice"] = UserContext("alice", "alice", "team", "团队")
        store.passwords["alice"] = hash_password("password-123")
        assert await service.login("alice", "password-123")
        with pytest.raises(SmsRequestError, match="密码错误"):
            await service.bind_phone("alice", "wrong-password", "13800138000", "a" * 32, "123456")
        assert sms.checks == []
        sent = await service.send_sms_challenge("13800138000", "bind", "203.0.113.9", "alice")
        assert sms.sent[0].template_code == "100002"
        assert sms.sent[0].scheme_name == "prod-bind"
        bound = await service.bind_phone("alice", "password-123", "13800138000", sent["challenge_id"], "123456")
        assert bound == {"ok": True, "phone_tail": "8000"}
        with pytest.raises(SmsRequestError, match="已绑定手机号"):
            await service.send_sms_challenge("13900139000", "bind", "203.0.113.9", "alice")
        clock.advance(61)
        login_sent = await service.send_sms_challenge("13800138000", "login", "203.0.113.9")
        _token, user_id = await service.login_with_sms("13800138000", login_sent["challenge_id"], "123456")
        assert user_id == "alice"

        store.users["alice"] = replace(store.users["alice"], tenant_status="deleted")
        clock.advance(61)
        blocked = await service.send_sms_challenge("13800138000", "login", "203.0.113.9")
        with pytest.raises(SmsRequestError, match="无法用于登录"):
            await service.login_with_sms("13800138000", blocked["challenge_id"], "123456")
        clock.advance(61)
        again = await service.send_sms_challenge("13800138000", "register", "203.0.113.9")
        with pytest.raises(SmsRequestError, match="已绑定其他账号"):
            await service.register_with_sms(
                "cindy", "password-123", "password-123", "13800138000", again["challenge_id"], "123456",
            )
        assert "cindy" not in store.users

        store.users["alice"] = replace(store.users["alice"], tenant_status="active", tenant_enabled=False)
        clock.advance(61)
        disabled = await service.send_sms_challenge("13800138000", "login", "203.0.113.9")
        token, user_id = await service.login_with_sms("13800138000", disabled["challenge_id"], "123456")
        assert user_id == "alice"
        assert (await service.session(token)).tenant_enabled is False
        with pytest.raises(SmsRequestError, match="管理员"):
            await service.bind_phone("admin", "password-123", "13900139000", "b" * 32, "123456")

    asyncio.run(run())


def test_disabled_sms_keeps_password_login_and_enabled_routes_are_public(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            challenge = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "login",
            })
            assert challenge.status_code == 403
            assert challenge.json()["detail"] == "短信验证未启用。"
            assert "13800138000" not in challenge.text
            assert (await client.post("/api/auth/register", json={
                "user_id": "alice", "password": "password-123", "confirm_password": "password-123",
                "phone": "13800138000", "challenge_id": "a" * 32, "code": "123456",
            })).status_code == 403
            logged = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            assert logged.status_code == 200
            use_access_token(client, logged)
            assert (await client.post("/api/auth/phone/bind", json={
                "password": "password-123", "phone": "13800138000",
                "challenge_id": "a" * 32, "code": "123456",
            })).status_code == 403
            assert (await client.post(
                "/api/auth/sms/challenges",
                json={"phone": "13800138000", "purpose": "login"},
                headers={"Sec-Fetch-Site": "cross-site"},
            )).status_code == 403

        enabled = create_app()
        store = MemoryPhoneStore()
        sms = FakeSms()
        enabled.state.chat = SimpleNamespace(storage=store, ready=True, status=AsyncMockStatus())
        enabled.state.sms_provider = sms
        enabled.state.sms_settings = sms_settings()
        enabled.state.sms_clock = Clock()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(enabled), base_url="http://test") as client:
            extra = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "login", "code": "123456",
            })
            assert extra.status_code == 422
            sent = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "register",
            })
            assert sent.status_code == 200, sent.text
            body = sent.json()
            assert set(body) == {"challenge_id", "expires_in", "resend_after"}
            registered = await client.post("/api/auth/register", json={
                "user_id": "alice", "password": "password-123", "confirm_password": "password-123",
                "phone": "13800138000", "challenge_id": body["challenge_id"], "code": "123456",
            })
            assert registered.status_code == 200
            assert _claims(registered.json()["access_token"])["sub"] == "alice"
            use_access_token(client, registered)
            session = await client.get("/api/auth/session")
            assert session.json()["phone_tail"] == "8000"
            assert "tenant_enabled" not in session.json()

    asyncio.run(run())
    text = (tmp_path / "security-audit" / f"audit-{date.today().isoformat()}.jsonl").read_text(encoding="utf-8")
    assert "13800138000" not in text
    assert "password-123" not in text
    assert "123456" not in text
    assert "尾号8000" in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert {row["operation"] for row in rows} >= {"发送短信验证码", "注册账号"}


class AsyncMockStatus:
    async def __call__(self):
        return {"ready": True}


def test_account_lock_conflict_can_retry_the_same_code():
    async def run():
        store = MemoryPhoneStore()
        service = build(store)
        sent = await service.send_sms_challenge("13800138000", "register", "203.0.113.10")
        original = store.register_verified_user

        async def fail_once(**kwargs):
            store.register_verified_user = original
            raise AccountConflict("当前有请求正在执行，请稍后重试。")

        store.register_verified_user = fail_once
        with pytest.raises(AccountConflict):
            await service.register_with_sms(
                "alice", "password-123", "password-123", "13800138000", sent["challenge_id"], "123456",
            )
        assert store.challenges[sent["challenge_id"]]["status"] == "sent"
        assert store.verifications == {}
        token = await service.register_with_sms(
            "alice", "password-123", "password-123", "13800138000", sent["challenge_id"], "123456",
        )
        assert _claims(token)["sub"] == "alice"
        assert list(store.verifications) == ["alice"]

    asyncio.run(run())


def test_admin_sees_masked_registration_record_and_members_cannot(monkeypatch):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        store = MemoryPhoneStore()
        store.users["admin"] = UserContext("admin", "管理员", "system", "系统", "owner")
        store.passwords["admin"] = hash_password("admin-password")
        app.state.chat = SimpleNamespace(storage=store, ready=True, status=AsyncMockStatus())
        app.state.sms_provider = FakeSms()
        app.state.sms_settings = sms_settings()
        app.state.sms_clock = Clock()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            sent = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "register",
            })
            assert sent.status_code == 200, sent.text
            challenge_id = sent.json()["challenge_id"]
            registered = await client.post("/api/auth/register", json={
                "user_id": "alice", "password": "password-123", "confirm_password": "password-123",
                "phone": "13800138000", "challenge_id": challenge_id, "code": "123456",
            })
            assert registered.status_code == 200, registered.text
            use_access_token(client, registered)
            denied = await client.get("/api/admin/users")
            assert denied.status_code == 403
            assert challenge_id not in denied.text
            assert "verification_id" not in (await client.get("/api/auth/session")).json()
            logged = await client.post("/api/auth/login", json={
                "user_id": "admin", "password": "admin-password",
            })
            assert logged.status_code == 200, logged.text
            use_access_token(client, logged)
            listed = await client.get("/api/admin/users")
            assert listed.status_code == 200
            items = {item["user_id"]: item for item in listed.json()["items"]}
            assert items["alice"]["phone_verified"] is True
            assert items["alice"]["phone_tail"] == "8000"
            assert items["alice"]["verification_method"] == "短信验证码"
            assert items["alice"]["verification_id"] == challenge_id
            assert items["alice"]["verified_at"]
            assert items["alice"]["created_at"]
            assert items["admin"]["phone_verified"] is False
            assert items["admin"]["verification_id"] is None
            assert "13800138000" not in listed.text
            assert "123456" not in listed.text
            assert "password-123" not in listed.text
            assert "phone_hmac" not in listed.text
            stamp = items["alice"]["verified_at"]
            await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            again = await client.get("/api/admin/users")
            alice = next(item for item in again.json()["items"] if item["user_id"] == "alice")
            assert alice["verification_id"] == challenge_id
            assert alice["verified_at"] == stamp

    asyncio.run(run())


def test_reset_password_with_phone_flow_and_guards():
    async def run():
        clock = Clock()
        sms = FakeSms()
        store = MemoryPhoneStore()
        service = build(store, sms, clock, registration_enabled=False, sms_template_reset="100003")
        store.users["alice"] = UserContext("alice", "alice", "team", "团队")
        store.passwords["alice"] = hash_password("old-password")
        old_token = await service.login("alice", "old-password")
        bind_sent = await service.send_sms_challenge("13800138000", "bind", "203.0.113.10", "alice")
        await service.bind_phone("alice", "old-password", "13800138000", bind_sent["challenge_id"], "123456")

        # Mismatched or short password fails before checking SMS
        clock.advance(61)
        sent = await service.send_sms_challenge("13800138000", "reset", "203.0.113.10")
        assert sms.sent[-1].scheme_name == "prod-reset"
        assert sms.sent[-1].template_code == "100003"
        checks_before = len(sms.checks)
        with pytest.raises(SmsRequestError, match="不一致"):
            await service.reset_password_with_sms(
                "13800138000", sent["challenge_id"], "123456", "new-password", "other-password",
            )
        with pytest.raises(SmsRequestError, match="5～128"):
            await service.reset_password_with_sms(
                "13800138000", sent["challenge_id"], "123456", "1234", "1234",
            )
        assert len(sms.checks) == checks_before

        # Cross-purpose challenge is rejected without calling provider
        clock.advance(61)
        login_sent = await service.send_sms_challenge("13800138000", "login", "203.0.113.10")
        with pytest.raises(SmsRequestError, match="无效或已过期"):
            await service.reset_password_with_sms(
                "13800138000", login_sent["challenge_id"], "123456", "new-password", "new-password",
            )
        assert len(sms.checks) == checks_before

        # Wrong code can be retried; lock conflict settles back to sent and retries
        sms.check_result = SmsCallResult("unknown")
        with pytest.raises(SmsRequestError, match="验证码不正确"):
            await service.reset_password_with_sms(
                "13800138000", sent["challenge_id"], "000000", "new-password", "new-password",
            )
        sms.check_result = SmsCallResult("pass")
        original = store.reset_password_verified_phone

        async def fail_once(**kwargs):
            store.reset_password_verified_phone = original
            raise AccountConflict("当前有请求正在执行，请稍后重试。")

        store.reset_password_verified_phone = fail_once
        with pytest.raises(AccountConflict):
            await service.reset_password_with_sms(
                "13800138000", sent["challenge_id"], "123456", "new-password", "new-password",
            )
        assert store.challenges[sent["challenge_id"]]["status"] == "sent"

        reset_user = await service.reset_password_with_sms(
            "+86 138-0013-8000", sent["challenge_id"], "123456", "new-password", "new-password",
        )
        assert reset_user == "alice"
        with pytest.raises(ValueError, match="用户 ID 或密码错误"):
            await service.login("alice", "old-password")
        assert await service.login("alice", "new-password")
        assert (await service.session(old_token)).user_id == "alice"

        # Replay fails
        with pytest.raises(SmsRequestError, match="无效或已过期"):
            await service.reset_password_with_sms(
                "13800138000", sent["challenge_id"], "123456", "another-pass", "another-pass",
            )

        # Unregistered phone consumes challenge and fails
        unreg = await service.send_sms_challenge("13900139000", "reset", "203.0.113.10")
        with pytest.raises(SmsRequestError, match="尚未绑定账号"):
            await service.reset_password_with_sms(
                "13900139000", unreg["challenge_id"], "123456", "new-password", "new-password",
            )
        assert store.challenges[unreg["challenge_id"]]["status"] == "consumed"

        # Deleted user cannot reset password
        store.users["alice"] = replace(store.users["alice"], tenant_status="deleted")
        clock.advance(61)
        deleted_sent = await service.send_sms_challenge("13800138000", "reset", "203.0.113.10")
        with pytest.raises(SmsRequestError, match="无法用于找回密码"):
            await service.reset_password_with_sms(
                "13800138000", deleted_sent["challenge_id"], "123456", "deleted-pass", "deleted-pass",
            )

    asyncio.run(run())


def test_reset_password_routes_and_audit_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "")

    async def run():
        disabled = create_app()
        disabled.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(disabled), base_url="http://test") as client:
            assert (await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "reset",
            })).status_code == 403
            assert (await client.post("/api/auth/password/reset", json={
                "phone": "13800138000", "challenge_id": "a" * 32, "code": "123456",
                "password": "new-password", "confirm_password": "new-password",
            })).status_code == 403
            assert (await client.post(
                "/api/auth/password/reset",
                json={
                    "phone": "13800138000", "challenge_id": "a" * 32, "code": "123456",
                    "password": "new-password", "confirm_password": "new-password",
                },
                headers={"Sec-Fetch-Site": "cross-site"},
            )).status_code == 403

        enabled = create_app()
        store = MemoryPhoneStore()
        sms = FakeSms()
        clock = Clock()
        enabled.state.chat = SimpleNamespace(storage=store, ready=True, status=AsyncMockStatus())
        enabled.state.sms_provider = sms
        enabled.state.sms_settings = sms_settings()
        enabled.state.sms_clock = clock
        async with httpx.AsyncClient(transport=httpx.ASGITransport(enabled), base_url="http://test") as client:
            reg = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "register",
            })
            assert reg.status_code == 200
            registered = await client.post("/api/auth/register", json={
                "user_id": "alice", "password": "old-password", "confirm_password": "old-password",
                "phone": "13800138000", "challenge_id": reg.json()["challenge_id"], "code": "123456",
            })
            assert registered.status_code == 200
            verification_before = dict(store.verifications["alice"])

            clock.advance(61)
            extra = await client.post("/api/auth/password/reset", json={
                "phone": "13800138000", "challenge_id": "a" * 32, "code": "123456",
                "password": "new-password-999", "confirm_password": "new-password-999",
                "unexpected": True,
            })
            assert extra.status_code == 422

            sent = await client.post("/api/auth/sms/challenges", json={
                "phone": "13800138000", "purpose": "reset",
            })
            assert sent.status_code == 200, sent.text
            reset = await client.post("/api/auth/password/reset", json={
                "phone": "13800138000", "challenge_id": sent.json()["challenge_id"], "code": "654321",
                "password": "new-password-999", "confirm_password": "new-password-999",
            })
            assert reset.status_code == 200, reset.text
            assert reset.headers["cache-control"] == "no-store"
            assert reset.json() == {"ok": True, "user_id": "alice"}
            assert store.verifications["alice"] == verification_before

            assert (await client.post("/api/auth/login", json={
                "user_id": "alice", "password": "old-password",
            })).status_code == 401
            assert (await client.post("/api/auth/login", json={
                "user_id": "alice", "password": "new-password-999",
            })).status_code == 200

    asyncio.run(run())
    text = (tmp_path / "security-audit" / f"audit-{date.today().isoformat()}.jsonl").read_text(encoding="utf-8")
    assert "13800138000" not in text
    assert "new-password-999" not in text
    assert "654321" not in text
    assert "尾号8000" in text
    rows = [json.loads(line) for line in text.splitlines()]
    reset_rows = [row for row in rows if row["operation"] == "找回密码" and row["result"] == "成功"]
    assert len(reset_rows) == 1
    assert reset_rows[0]["account"] == "alice"
    assert reset_rows[0]["target_account"] == "尾号8000"
