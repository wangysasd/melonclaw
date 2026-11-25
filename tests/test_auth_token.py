"""签发、验签和过期不访问数据库。"""

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from melonclaw.api.app import create_app
from melonclaw.core.auth_token import issue_token, token_subject
from melonclaw.core.config import load_settings
from melonclaw.repository.users import UserRepositoryMixin

SECRET = "jwt-unit-secret-must-be-32-characters"
ROOT = Path(__file__).resolve().parents[1] / "src" / "melonclaw"


def test_round_trip_reads_subject_without_extra_claims():
    token = issue_token("alice", SECRET, 2, now=1_700_000_000)
    assert token_subject(token, SECRET, now=1_700_000_000) == "alice"
    assert token_subject(token, SECRET, now=1_700_000_000 + 2 * 3600 - 1) == "alice"
    assert token_subject(token, SECRET, now=1_700_000_000 + 2 * 3600) is None


def test_tampered_subject_or_wrong_secret_is_rejected():
    token = issue_token("alice", SECRET, 1, now=1_700_000_000)
    header, _payload, signature = token.split(".")
    forged = f"{header}.eyJzdWIiOiJhZG1pbiIsImV4cCI6MTcwMDAzNjAwMH0.{signature}"
    assert token_subject(forged, SECRET, now=1_700_000_000) is None
    assert token_subject(token, SECRET + "-other", now=1_700_000_000) is None
    assert token_subject("not-a-token", SECRET) is None


def test_secret_and_ttl_configuration(monkeypatch):
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", SECRET)
    monkeypatch.delenv("MELONCLAW_TOKEN_TTL_HOURS", raising=False)
    assert load_settings().token_ttl_hours == 1000
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "  ")
    assert load_settings().token_ttl_hours == 1000
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "24")
    assert load_settings().token_ttl_hours == 24
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", "short")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", SECRET)
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "0")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "nope")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.delenv("MELONCLAW_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", " \t")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", "x" * 31)
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "-1")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "1.5")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("MELONCLAW_TOKEN_TTL_HOURS", "2")
    assert load_settings().token_ttl_hours == 2
    monkeypatch.setenv("MELONCLAW_JWT_SECRET", "short")
    with pytest.raises(RuntimeError):
        create_app()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _json(value: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


def _signed(header: dict, payload: dict, secret: str = SECRET) -> str:
    signing = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{_b64(signature)}"


def test_issued_payload_contains_only_subject_and_expiry():
    token = issue_token("alice", SECRET, 2, now=1_700_000_000)
    header, payload, _signature = token.split(".")
    assert _json(header) == {"alg": "HS256", "typ": "JWT"}
    assert _json(payload) == {"sub": "alice", "exp": 1_700_000_000 + 2 * 3600}


def test_rejects_tampered_signature_extra_claims_and_wrong_types():
    now = 1_700_000_000
    valid = _signed({"alg": "HS256", "typ": "JWT"}, {"sub": "alice", "exp": now + 10})
    assert token_subject(valid, SECRET, now=now) == "alice"
    flipped = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    assert token_subject(flipped, SECRET, now=now) is None
    extra = {"sub": "alice", "exp": now + 10, "iss": "melonclaw", "role": "admin"}
    assert token_subject(_signed({"alg": "HS256", "typ": "JWT"}, extra), SECRET, now=now) is None
    assert token_subject(_signed({"alg": "none", "typ": "JWT"}, {"sub": "alice", "exp": now + 10}), SECRET, now=now) is None
    assert token_subject(_signed({"alg": "HS256", "typ": "JWT"}, {"sub": "alice", "exp": now + 10.5}), SECRET, now=now) is None
    assert token_subject(_signed({"alg": "HS256", "typ": "JWT"}, {"sub": "alice", "exp": True}), SECRET, now=now) is None
    assert token_subject(_signed({"alg": "HS256", "typ": "JWT"}, {"sub": "", "exp": now + 10}), SECRET, now=now) is None


def test_active_user_query_requires_active_status_and_ignores_tenant_enabled():
    captured = {}

    class Connection:
        async def execute(self, query):
            captured["query"] = query

            class Mapped:
                def first(self):
                    return None

            class Result:
                def mappings(self):
                    return Mapped()

            return Result()

    class Probe(UserRepositoryMixin):
        pass

    probe = Probe()
    asyncio.run(probe.active_user("alice", connection=Connection()))
    compiled = captured["query"].compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "tenant_status" in sql
    assert "tenant_enabled" in sql
    assert "tenants.enabled is true" not in sql
    assert "alice" in compiled.params.values()
    assert "active" in compiled.params.values()

    asyncio.run(probe.get_user_context("alice", connection=Connection()))
    context_sql = str(captured["query"].compile(dialect=postgresql.dialect())).lower()
    assert "tenants.enabled is true" in context_sql


def test_runtime_does_not_read_or_write_auth_sessions():
    schema = (ROOT / "database" / "schema.py").read_text()
    assert "auth_sessions = Table(" in schema
    allowed = {
        "database/schema.py",
        "database/constants.py",
        "database/updates/account_management.py",
    }
    hits = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if "auth_sessions" in path.read_text() and path.relative_to(ROOT).as_posix() not in allowed
    ]
    assert hits == []
