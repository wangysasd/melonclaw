"""安全审计写入独立文件：不改表，不记录口令和令牌。"""

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
from test_account_api import MemoryAccounts

from melonclaw.api.app import create_app
from melonclaw.api.audit import append_audit, describe_request
from melonclaw.core.config import load_settings


def _lines(directory):
    files = sorted(directory.glob("audit-*.jsonl"))
    return [json.loads(line) for path in files for line in path.read_text().splitlines()]


def test_audit_dir_follows_data_root_or_override(monkeypatch, tmp_path):
    monkeypatch.delenv("MELONCLAW_AUDIT_DIR", raising=False)
    monkeypatch.setenv("MELONCLAW_DATA_DIR", str(tmp_path / "data"))
    assert load_settings().audit_dir == (tmp_path / "data").resolve() / "audit"

    outside = tmp_path / "elsewhere"
    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(outside))
    assert load_settings().audit_dir == outside.resolve()


def test_append_drops_secrets_and_prunes_expired_files(tmp_path):
    directory = tmp_path / "audit"
    expired = directory / "audit-2020-01-01.jsonl"
    directory.mkdir()
    expired.write_text('{"account":"old"}\n')
    kept = date.today() - timedelta(days=179)
    (directory / f"audit-{kept.isoformat()}.jsonl").write_text('{"account":"kept"}\n')
    append_audit(
        {
            "account": "alice",
            "operation": "登录",
            "result": "成功",
            "status": 200,
            "password": "secret-value",
            "authorization": "Bearer secret-token",
            "device_id": "a" * 32,
            "source_port": 443,
        },
        directory,
    )
    assert not expired.exists()
    assert (directory / f"audit-{kept.isoformat()}.jsonl").exists()
    written = _lines(directory)
    assert written[-1]["account"] == "alice"
    assert "password" not in written[-1]
    assert "secret-value" not in (directory / f"audit-{date.today().isoformat()}.jsonl").read_text()
    assert "secret-token" not in (directory / f"audit-{date.today().isoformat()}.jsonl").read_text()
    assert oct((directory / f"audit-{date.today().isoformat()}.jsonl").stat().st_mode & 0o777) == "0o600"


def test_login_records_forwarded_client_and_not_the_password(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        transport = httpx.ASGITransport(app, client=("127.0.0.1", 9))
        async with httpx.AsyncClient(transport=transport, base_url="http://melonclaw.test") as client:
            headers = {
                "User-Agent": "AuditBrowser",
                "X-Real-IP": "203.0.113.10",
                "X-Real-Port": "54321",
                "X-Forwarded-Host": "melonclaw.cn",
                "X-Forwarded-Port": "443",
                "X-Forwarded-Proto": "https",
                "Sec-CH-UA-Platform": '"macOS"',
            }
            failed = await client.post(
                "/api/auth/login",
                json={"user_id": "alice", "password": "wrong-password"},
                headers=headers,
            )
            assert failed.status_code == 401
            logged = await client.post(
                "/api/auth/login",
                json={"user_id": "alice", "password": "password-123"},
                headers={**headers, "Cookie": failed.headers["set-cookie"].split(";", 1)[0]},
            )
            assert logged.status_code == 200
            assert "set-cookie" not in logged.headers
            untouched = await client.get("/api/auth/config")
            assert untouched.status_code == 200

    asyncio.run(run())
    rows = _lines(tmp_path / "security-audit")
    assert [row["result"] for row in rows] == ["失败", "成功"]
    assert rows[1]["account"] == "alice"
    assert rows[1]["operation"] == "登录"
    assert rows[1]["route"] == "POST /api/auth/login"
    assert rows[1]["source_ip"] == "203.0.113.10"
    assert rows[1]["source_port"] == 54321
    assert rows[1]["destination_address"] == "melonclaw.cn"
    assert rows[1]["destination_port"] == 443
    assert rows[1]["user_agent"] == "AuditBrowser"
    assert rows[1]["client_platform"] == "macOS"
    assert len(rows[1]["device_id"]) == 32
    blob = "".join(path.read_text() for path in (tmp_path / "security-audit").glob("audit-*.jsonl"))
    assert "password-123" not in blob
    assert "wrong-password" not in blob


def test_authenticated_account_comes_from_request_state():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/logout",
        "route": SimpleNamespace(path="/api/auth/logout"),
        "scheme": "http",
        "client": ("198.51.100.8", 4000),
        "server": ("203.0.113.2", 8000),
        "headers": [(b"host", b"melonclaw.test")],
        "state": {"user": SimpleNamespace(user_id="alice"), "audit_target": "bob"},
    }
    described = describe_request(scope, 200, "c" * 32)
    assert described["account"] == "alice"
    assert described["target_account"] == "bob"
    assert described["operation"] == "登出"
    assert described["result"] == "成功"


def test_public_client_cannot_spoof_forwarded_address():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "scheme": "http",
        "client": ("198.51.100.7", 5555),
        "server": ("203.0.113.2", 8000),
        "headers": [
            (b"host", b"melonclaw.test"),
            (b"x-real-ip", b"203.0.113.10"),
            (b"x-real-port", b"1"),
            (b"x-forwarded-host", b"evil.example"),
            (b"x-forwarded-port", b"443"),
        ],
        "state": SimpleNamespace(audit_account="alice"),
    }
    described = describe_request(scope, 401, "b" * 32)
    assert described["source_ip"] == "198.51.100.7"
    assert described["source_port"] == 5555
    assert described["destination_address"] == "melonclaw.test"
    assert described["destination_port"] == 8000
    assert described["account"] == "alice"
