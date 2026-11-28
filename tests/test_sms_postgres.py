"""短信表的隔离升级、唯一约束和并发注册。不使用业务库 public 数据。"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from account_fixtures import use_access_token
from sms_fakes import FakeSms, sms_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.api.app import create_app
from melonclaw.core.passwords import hash_password
from melonclaw.database import update_runner
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.update_runner import apply_schema_updates
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.repository import BusinessRepository
from melonclaw.repository.sms_auth import ChallengeLost, SmsQuotaError
from melonclaw.services.sms_provider import SmsCallResult, interpret_check

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 PostgreSQL 测试连接",
)


def _now():
    return datetime(2026, 11, 26, 1, 0, tzinfo=UTC)


async def _database():
    url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
    schema = "test_sms_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    async with engine.begin() as connection:
        await connection.execute(text("""
            CREATE TABLE tenants (
                tenant_id VARCHAR(64) PRIMARY KEY,
                tenant_name_zh VARCHAR(64) NOT NULL UNIQUE,
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await connection.execute(text("""
            CREATE TABLE users (
                user_id VARCHAR(64) PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id),
                user_name_zh VARCHAR(64) NOT NULL,
                tenant_role VARCHAR(32) NOT NULL DEFAULT 'member',
                tenant_status VARCHAR(16) NOT NULL DEFAULT 'active',
                password_hash TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await connection.execute(text(
            "INSERT INTO tenants (tenant_id, tenant_name_zh, enabled, created_at) "
            "VALUES ('team', '原租户', true, :now)"
        ), {"now": _now()})
        await connection.execute(text(
            "INSERT INTO users (user_id, tenant_id, user_name_zh, password_hash, created_at) "
            "VALUES ('alice', 'team', '小王', 'kept-hash', :now)"
        ), {"now": _now()})
    return control, engine, schema


def test_sms_upgrade_preserves_accounts_and_enforces_one_phone(monkeypatch):
    updates = update_runner.UPDATES
    monkeypatch.setattr(
        update_runner, "UPDATES",
        tuple(item for item in update_runner.UPDATES if item.update_id == "20261126_sms_auth"),
    )

    async def run():
        control, engine, schema = await _database()
        database = type("DB", (), {"engine": engine})()
        try:
            assert await apply_schema_updates(database) == ("20261126_sms_auth",)
            monkeypatch.setattr(update_runner, "UPDATES", tuple(
                item for item in updates if item.update_id in {
                    "20261126_sms_auth",
                    "20261126_registration_verification",
                    "20261128_sms_reset_password",
                }
            ))
            assert await apply_schema_updates(database) == (
                "20261126_registration_verification",
                "20261128_sms_reset_password",
            )
            assert await apply_schema_updates(database) == ()
            repo = BusinessRepository(database)
            now = _now()
            await repo.reserve_sms_challenge(
                phone_hmac="a" * 64, purpose="register", user_id=None, scheme_name="prod-register",
                out_id="out-1", challenge_id="c" * 32, expires_at=now + timedelta(minutes=5),
                now=now, day="2026-11-26", interval_seconds=60, phone_daily_limit=10,
                ip_daily_limit=30, global_daily_limit=200, source_ip="203.0.113.10", stale_seconds=10,
            )
            assert await repo.finish_sms_send("c" * 32, "sent", now)
            with pytest.raises(SmsQuotaError):
                await repo.reserve_sms_challenge(
                    phone_hmac="a" * 64, purpose="login", user_id=None, scheme_name="prod-login",
                    out_id="out-2", challenge_id="d" * 32, expires_at=now + timedelta(minutes=5),
                    now=now, day="2026-11-26", interval_seconds=60, phone_daily_limit=10,
                    ip_daily_limit=30, global_daily_limit=200, source_ip="203.0.113.10",
                    stale_seconds=10,
                )
            later = now + timedelta(seconds=61)
            await repo.reserve_sms_challenge(
                phone_hmac="a" * 64, purpose="register", user_id=None, scheme_name="prod-register",
                out_id="out-3", challenge_id="e" * 32, expires_at=later + timedelta(minutes=5),
                now=later, day="2026-11-26", interval_seconds=60, phone_daily_limit=10,
                ip_daily_limit=30, global_daily_limit=200, source_ip="203.0.113.11", stale_seconds=10,
            )
            async with engine.connect() as connection:
                old = await connection.scalar(text(
                    "SELECT status FROM sms_challenges WHERE challenge_id = :id"
                ), {"id": "c" * 32})
                alice = await connection.scalar(text(
                    "SELECT password_hash FROM users WHERE user_id = 'alice'"
                ))
            assert old == "replaced"
            assert alice == "kept-hash"
            async with engine.begin() as connection:
                await connection.execute(text(
                    "UPDATE sms_challenges SET status = 'verifying' WHERE challenge_id = :id"
                ), {"id": "e" * 32})

            async def register(user_id):
                try:
                    await repo.register_verified_user(
                        challenge_id="e" * 32, generation=2, user_id=user_id, display_name=user_id,
                        password_hash="hash-a", phone_hmac="a" * 64, phone_tail="8000",
                        verified_at=later, now=later,
                    )
                    return "ok"
                except (AccountConflict, ChallengeLost):
                    return "conflict"

            results = await asyncio.gather(register("new-user"), register("other-user"))
            assert sorted(results) == ["conflict", "ok"]
            async with engine.connect() as connection:
                users = (await connection.execute(text(
                    "SELECT user_id FROM users WHERE user_id IN ('new-user', 'other-user') ORDER BY user_id"
                ))).scalars().all()
                phones = await connection.scalar(text("SELECT COUNT(*) FROM user_phones"))
                records = (await connection.execute(text(
                    "SELECT user_id, verification_id FROM registration_verifications"
                ))).all()
                tenants = await connection.scalar(text(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_id <> 'team'"
                ))
            assert users == ["new-user"] or users == ["other-user"]
            assert phones == 1
            assert tenants == 1
            assert records == [(users[0], "e" * 32)]
            async with engine.begin() as connection:
                await connection.execute(text(
                    "UPDATE users SET tenant_status = 'deleted' WHERE user_id = :id"
                ), {"id": users[0]})
                await connection.execute(text("""
                    INSERT INTO sms_challenges (
                        challenge_id, purpose, phone_hmac, generation, status, out_id, scheme_name,
                        attempts, expires_at, created_at, updated_at
                    ) VALUES (
                        :id, 'login', :phone, 1, 'verifying', 'out-login', 'prod-login',
                        1, :expires, :now, :now
                    )
                """), {
                    "id": "f" * 32, "phone": "a" * 64, "expires": later + timedelta(minutes=5),
                    "now": later,
                })
            outcome = await repo.login_verified_phone(
                challenge_id="f" * 32, generation=1, phone_hmac="a" * 64, now=later,
            )
            assert outcome["kind"] == "unavailable"
            async with engine.begin() as connection:
                await connection.execute(text("""
                    INSERT INTO sms_challenges (
                        challenge_id, purpose, phone_hmac, generation, status, out_id, scheme_name,
                        attempts, expires_at, created_at, updated_at
                    ) VALUES (
                        :id, 'register', :phone, 1, 'verifying', 'out-reg', 'prod-register',
                        1, :expires, :now, :now
                    )
                """), {
                    "id": "9" * 32, "phone": "a" * 64, "expires": later + timedelta(minutes=5),
                    "now": later,
                })
            await repo.register_verified_user(
                challenge_id="9" * 32, generation=1, user_id="cindy", display_name="cindy",
                password_hash="hash-b", phone_hmac="a" * 64, phone_tail="8000",
                verified_at=later, now=later,
            )
            async with engine.connect() as connection:
                owner = await connection.scalar(text("SELECT user_id FROM user_phones"))
                records = await connection.scalar(text("SELECT COUNT(*) FROM registration_verifications"))
            assert owner == "cindy"
            assert records == 2

            reset_time = later + timedelta(seconds=61)
            await repo.reserve_sms_challenge(
                phone_hmac="a" * 64, purpose="reset", user_id=None, scheme_name="prod-reset",
                out_id="out-reset", challenge_id="8" * 32, expires_at=reset_time + timedelta(minutes=5),
                now=reset_time, day="2026-11-26", interval_seconds=60, phone_daily_limit=10,
                ip_daily_limit=30, global_daily_limit=200, source_ip="203.0.113.12", stale_seconds=10,
            )
            assert await repo.finish_sms_send("8" * 32, "sent", reset_time)
            claimed = await repo.claim_sms_attempt(
                challenge_id="8" * 32, purpose="reset", phone_hmac="a" * 64,
                user_id=None, max_attempts=5, now=reset_time,
            )
            assert claimed is not None
            outcome = await repo.reset_password_verified_phone(
                challenge_id="8" * 32, generation=claimed["generation"], phone_hmac="a" * 64,
                password_hash="hash-reset", now=reset_time,
            )
            assert outcome == {"kind": "reset", "user_id": "cindy"}
            async with engine.connect() as connection:
                cindy_hash = await connection.scalar(text(
                    "SELECT password_hash FROM users WHERE user_id = 'cindy'"
                ))
                reset_status = await connection.scalar(text(
                    "SELECT status FROM sms_challenges WHERE challenge_id = :id"
                ), {"id": "8" * 32})
            assert cindy_hash == "hash-reset"
            assert reset_status == "consumed"
            with pytest.raises(ChallengeLost):
                await repo.reset_password_verified_phone(
                    challenge_id="8" * 32, generation=claimed["generation"], phone_hmac="a" * 64,
                    password_hash="hash-replay", now=reset_time,
                )
        finally:
            await engine.dispose()
            async with control.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())


def test_registration_verification_end_to_end(monkeypatch):
    monkeypatch.setenv("profile", "")
    monkeypatch.setattr(update_runner, "UPDATES", tuple(
        item for item in update_runner.UPDATES if item.update_id in {
            "20261126_sms_auth", "20261126_registration_verification",
        }
    ))

    async def run():
        control, engine, schema = await _database()
        database = SimpleNamespace(engine=engine)
        try:
            await apply_schema_updates(database)
            now = _now()
            async with engine.begin() as connection:
                await connection.execute(text("""
                    INSERT INTO tenants (tenant_id, tenant_name_zh, created_at)
                    VALUES ('system', '系统', :now)
                """), {"now": now})
                await connection.execute(text("""
                    INSERT INTO users (user_id, tenant_id, user_name_zh, password_hash, created_at)
                    VALUES ('admin', 'system', '管理员', :password, :now)
                """), {"now": now, "password": hash_password("admin-password")})
                await connection.execute(text("""
                    ALTER TABLE registration_verifications ADD CONSTRAINT rejected_test_record
                    CHECK (user_id <> 'rolled-back')
                """))

            sms = FakeSms()
            app = create_app()
            app.state.chat = SimpleNamespace(storage=BusinessRepository(database))
            app.state.sms_provider = sms
            app.state.sms_settings = sms_settings()
            app.state.sms_clock = lambda: now
            transport = httpx.ASGITransport(app)
            phone = "13800138000"
            password = "password-123"
            pass_result = interpret_check(SimpleNamespace(
                code="OK", success=True, model=SimpleNamespace(verify_result="PASS"),
            ))

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                async def send(purpose="register"):
                    response = await client.post("/api/auth/sms/challenges", json={
                        "phone": phone, "purpose": purpose,
                    })
                    assert response.status_code == 200, response.text
                    return response.json()["challenge_id"]

                async def register(user_id, challenge_id):
                    return await client.post("/api/auth/register", json={
                        "user_id": user_id, "password": password, "confirm_password": password,
                        "phone": phone, "challenge_id": challenge_id, "code": "123456",
                    })

                def counts(connection):
                    return connection.execute(text("""
                        SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM tenants),
                               (SELECT count(*) FROM user_phones),
                               (SELECT count(*) FROM registration_verifications)
                    """))

                first = await send()
                sms.check_result = interpret_check(SimpleNamespace(
                    code="OK", success=True, model=SimpleNamespace(verify_result="UNKNOWN"),
                ))
                assert (await register("bad-code", first)).status_code == 422
                sms.check_result = SmsCallResult("timeout")
                assert (await register("unknown-result", first)).status_code == 503
                assert (await register("replayed", first)).status_code == 422
                now += timedelta(seconds=61)
                expiring = await send()

                async def expired_while_checking(request):
                    nonlocal now
                    assert request.out_id == sms.sent[-1].out_id
                    now += timedelta(seconds=app.state.sms_settings.sms_code_ttl_seconds)
                    return pass_result

                sms.check = expired_while_checking
                assert (await register("expired", expiring)).status_code == 422
                sms.check = FakeSms.check.__get__(sms, FakeSms)
                sms.check_result = pass_result
                rollback = await send()
                rolled_back = await register("rolled-back", rollback)
                assert rolled_back.status_code == 503, rolled_back.text
                async with engine.connect() as connection:
                    assert (await counts(connection)).one() == (2, 2, 0, 0)
                    assert await connection.scalar(text("""
                        SELECT status FROM sms_challenges WHERE challenge_id = :id
                    """), {"id": rollback}) == "verifying"

                now += timedelta(seconds=61)
                challenge = await send()
                submitted_at = now
                checking = asyncio.Event()
                release = asyncio.Event()

                async def slow_pass(request):
                    assert request.out_id == sms.sent[-1].out_id
                    checking.set()
                    await release.wait()
                    return pass_result

                sms.check = slow_pass
                pending = asyncio.create_task(register("registered", challenge))
                try:
                    await asyncio.wait_for(checking.wait(), timeout=5)
                    duplicate = await asyncio.wait_for(register("duplicate", challenge), timeout=5)
                    assert duplicate.status_code == 422, duplicate.text
                    now += timedelta(seconds=4)
                finally:
                    release.set()
                    registered = await pending
                assert registered.status_code == 200, registered.text
                assert (await register("replayed", challenge)).status_code == 422
                async with engine.connect() as connection:
                    assert (await counts(connection)).one() == (3, 3, 1, 1)

                use_access_token(client, registered)
                identity = (await client.get("/api/auth/session")).json()
                assert identity["user_id"] == "registered"
                assert identity["tenant_id"] not in {"team", "system"}
                assert identity["tenant_role"] == "member"
                assert "verification_id" not in identity
                for suffix in ("", "?include_deleted_registrations=true"):
                    denied = await client.get(f"/api/admin/users{suffix}")
                    assert denied.status_code == 403
                    assert challenge not in denied.text

                admin = await client.post("/api/auth/login", json={
                    "user_id": "admin", "password": "admin-password",
                })
                assert admin.status_code == 200
                use_access_token(client, admin)
                listed = await client.get("/api/admin/users")
                assert listed.status_code == 200
                assert listed.headers["cache-control"] == "no-store"
                items = {row["user_id"]: row for row in listed.json()["items"]}
                assert items["alice"]["phone_verified"] is False
                assert items["admin"]["phone_verified"] is False
                record = items["registered"]
                assert record["phone_verified"] is True
                assert record["verification_id"] == challenge
                assert record["verification_method"] == "短信验证码"
                assert record["phone_tail"] == "8000"
                assert datetime.fromisoformat(record["created_at"]) >= datetime.fromisoformat(record["verified_at"])
                assert datetime.fromisoformat(record["verified_at"]) == submitted_at + timedelta(seconds=4)
                for private in (phone, password, "phone_hmac", "password_hash", "access_token"):
                    assert private not in listed.text

                signed_in = await client.post("/api/auth/login", json={
                    "user_id": "registered", "password": password,
                })
                assert signed_in.status_code == 200
                use_access_token(client, signed_in)
                assert (await client.get("/api/auth/session")).json() == identity
                now += timedelta(days=3)
                sms.check = FakeSms.check.__get__(sms, FakeSms)
                login_challenge = await send("login")
                signed_in = await client.post("/api/auth/login/sms", json={
                    "phone": phone, "challenge_id": login_challenge, "code": "123456",
                })
                assert signed_in.status_code == 200
                use_access_token(client, signed_in)
                assert (await client.get("/api/auth/session")).json() == identity
                use_access_token(client, admin)
                updated = (await client.get("/api/admin/users")).json()["items"]
                assert next(row for row in updated if row["user_id"] == "registered") == record

                async with engine.begin() as connection:
                    assert await connection.scalar(text("""
                        SELECT challenge_id FROM sms_challenges WHERE challenge_id = :challenge
                    """), {"challenge": challenge}) is None
                    await connection.execute(text("""
                        UPDATE users SET tenant_status = 'deleted'
                        WHERE user_id IN ('registered', 'alice')
                    """))
                default_users = (await client.get("/api/admin/users")).json()["items"]
                assert [row["user_id"] for row in default_users] == ["admin"]
                archived = (await client.get("/api/admin/users?include_deleted_registrations=true")).json()["items"]
                assert archived == [items["admin"], {**record, "tenant_status": "deleted"}]
        finally:
            await engine.dispose()
            async with control.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())
