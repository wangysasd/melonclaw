"""安全审计写入独立文件：不改表，不记录口令和令牌。"""

import asyncio
import errno
import json
import logging
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from test_account_api import MemoryAccounts

from melonclaw.api import audit, audit_alert
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
    today = date(2026, 9, 1)
    # 3 月 1 日到 9 月 1 日正好六个自然月，共 184 天。
    expired = directory / "audit-2026-02-28.jsonl"
    kept = directory / "audit-2026-03-01.jsonl"
    directory.mkdir()
    expired.write_text('{"account":"old"}\n')
    kept.write_text('{"account":"kept"}\n')
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
        today=today,
    )
    assert not expired.exists()
    assert kept.exists()
    written = _lines(directory)
    assert written[-1]["account"] == "alice"
    assert "password" not in written[-1]
    assert "secret-value" not in (directory / f"audit-{today.isoformat()}.jsonl").read_text()
    assert "secret-token" not in (directory / f"audit-{today.isoformat()}.jsonl").read_text()
    assert (
        oct((directory / f"audit-{today.isoformat()}.jsonl").stat().st_mode & 0o777)
        == "0o600"
    )


def test_login_records_forwarded_client_and_not_the_password(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "")

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        transport = httpx.ASGITransport(app, client=("127.0.0.1", 9))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://melonclaw.test"
        ) as client:
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


class _AlertCatcher:
    def __init__(self, statuses, delay=0):
        self.statuses = list(statuses)
        self.delay = delay
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.bodies.append(body)
                if outer.delay:
                    time.sleep(outer.delay)
                code = outer.statuses.pop(0) if outer.statuses else 204
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}/alerts"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def _scope():
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "route": SimpleNamespace(path="/api/auth/login"),
        "scheme": "http",
        "client": ("127.0.0.1", 9),
        "headers": [(b"host", b"melonclaw.test")],
        "state": SimpleNamespace(audit_account="alice"),
    }


def _messages(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def test_webhook_setting_rejects_a_bad_address_without_echoing_it(monkeypatch):
    marker = "do-not-echo-this-token"
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", f"https://user:{marker}@127.0.0.1/hook")
    with pytest.raises(RuntimeError, match="MELONCLAW_AUDIT_ALERT_WEBHOOK_URL") as caught:
        load_settings()
    assert marker not in str(caught.value)


def test_unconfigured_audit_failure_only_warns(monkeypatch, caplog, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("x")
    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(blocked / "audit"))

    def refuse(*args, **kwargs):
        raise AssertionError("unconfigured failure must not open a client")

    monkeypatch.setattr(audit_alert.httpx, "Client", refuse)
    with caplog.at_level(logging.WARNING):
        audit._write(_scope(), 401, "a" * 32)
    assert "安全审计日志写入失败" in _messages(caplog)
    assert "blocked" not in _messages(caplog)
    assert audit_alert.flush_audit_alerts(0.2)


def test_audit_write_failure_notifies_once_and_login_still_fails(monkeypatch, caplog, tmp_path):
    catcher = _AlertCatcher([204, 204])
    blocked = tmp_path / "blocked"
    blocked.write_text("x")
    monkeypatch.setenv("profile", "")
    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(blocked / "audit"))
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", catcher.url)
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS", "2")
    try:
        with caplog.at_level(logging.WARNING):

            async def run():
                app = create_app()
                app.state.chat = SimpleNamespace(storage=MemoryAccounts())
                transport = httpx.ASGITransport(app, client=("127.0.0.1", 9))
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://melonclaw.test"
                ) as client:
                    first = await client.post(
                        "/api/auth/login",
                        json={"user_id": "alice", "password": "wrong-password"},
                    )
                    second = await client.post(
                        "/api/auth/login",
                        json={"user_id": "alice", "password": "wrong-password"},
                    )
                assert first.status_code == 401
                assert second.status_code == 401

            asyncio.run(run())
            assert audit_alert.flush_audit_alerts(3)
        bodies = [json.loads(body) for body in catcher.bodies]
        assert len(bodies) == 1
        assert bodies[0]["service"] == "MelonClaw"
        assert bodies[0]["category"] == "directory"
        assert bodies[0]["time"]
        assert bodies[0]["suggestion"]
        rendered = json.dumps(bodies, ensure_ascii=False)
        assert "://" not in rendered
        assert "wrong-password" not in rendered
        assert "blocked" not in rendered
        text = _messages(caplog)
        assert "安全审计日志写入失败" in text
        assert "wrong-password" not in text
        assert catcher.url not in text
    finally:
        catcher.close()


def test_alert_retries_after_notify_failure_and_rearms_after_recovery(monkeypatch, tmp_path):
    catcher = _AlertCatcher([500, 204, 204])
    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", catcher.url)
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_RETRY_SECONDS", "300")
    clock = {"now": 0.0}
    monkeypatch.setattr(audit_alert, "_now", lambda: clock["now"])
    original = audit.append_audit
    mode = {"kind": "permission"}

    def fail_or_write(*args, **kwargs):
        kind = mode["kind"]
        if kind == "ok":
            return original(*args, **kwargs)
        if kind == "permission":
            raise PermissionError(13, "secret-path")
        raise OSError(errno.ENOSPC, "disk-marker")

    monkeypatch.setattr(audit, "append_audit", fail_or_write)
    try:
        audit._write(_scope(), 401, "c" * 32)
        assert audit_alert.flush_audit_alerts(3)
        assert len(catcher.bodies) == 1
        clock["now"] = 10
        audit._write(_scope(), 401, "c" * 32)
        assert audit_alert.flush_audit_alerts(1)
        assert len(catcher.bodies) == 1
        clock["now"] = 300
        audit._write(_scope(), 401, "c" * 32)
        assert audit_alert.flush_audit_alerts(3)
        assert len(catcher.bodies) == 2
        assert json.loads(catcher.bodies[1])["category"] == "permission"
        assert b"secret-path" not in catcher.bodies[1]
        clock["now"] = 310
        audit._write(_scope(), 401, "c" * 32)
        assert audit_alert.flush_audit_alerts(1)
        assert len(catcher.bodies) == 2
        mode["kind"] = "ok"
        audit._write(_scope(), 200, "c" * 32)
        mode["kind"] = "disk"
        clock["now"] = 311
        audit._write(_scope(), 500, "c" * 32)
        assert audit_alert.flush_audit_alerts(3)
        assert len(catcher.bodies) == 3
        assert json.loads(catcher.bodies[2])["category"] == "disk"
        assert b"disk-marker" not in catcher.bodies[2]
    finally:
        catcher.close()


def test_alert_send_does_not_block_the_request(monkeypatch):
    catcher = _AlertCatcher([204], delay=1.5)
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", catcher.url)
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_RETRY_SECONDS", "300")
    monkeypatch.setattr(
        audit,
        "append_audit",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError(13, "denied")),
    )
    try:
        started = time.monotonic()
        audit._write(_scope(), 401, "d" * 32)
        assert time.monotonic() - started < 0.5
        assert audit_alert.flush_audit_alerts(3)
        assert catcher.bodies
    finally:
        catcher.close()
