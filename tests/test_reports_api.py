"""投诉举报 HTTP 边界：匿名入口、越权和管理员办结。"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from account_fixtures import use_access_token
from report_memory import MemoryReports

from melonclaw.api.app import create_app
from melonclaw.core.reports import ReportRateLimiter


def _app(storage):
    app = create_app()
    app.state.chat = type("Chat", (), {"storage": storage})()
    app.state.report_limiter = ReportRateLimiter(
        submit_limit=20, lookup_limit=20, failure_limit=20, user_submit_limit=20,
    )
    return app


def _audit_text(tmp_path: Path) -> str:
    directory = tmp_path / "security-audit"
    files = list(directory.glob("audit-*.jsonl"))
    assert files, "写操作应留下审计文件"
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def test_anonymous_complaint_audit_and_admin_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "dev")
    storage = MemoryReports()
    body = "SECRET-BODY-XYZ"
    contact = "secret-caller@example.com"

    async def run():
        transport = httpx.ASGITransport(_app(storage))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/reports/channels")).status_code == 200
            invalid = await client.post("/api/reports/anonymous", json={
                "reason": "nope", "description": body, "contact": contact,
            })
            assert invalid.status_code == 422
            assert body not in invalid.text and contact not in invalid.text
            created = await client.post("/api/reports/anonymous", json={
                "reason": "illegal", "description": body, "contact": contact,
            })
            assert created.status_code == 201, created.text
            payload = created.json()
            secret = payload["lookup_code"]
            assert secret not in json.dumps(storage.rows, default=str)
            audit = _audit_text(tmp_path)
            assert "提交投诉" in audit
            assert secret not in audit and body not in audit and contact not in audit
            wrong = await client.post("/api/reports/lookup", json={
                "public_code": payload["public_code"], "lookup_code": "wrong-lookup-code",
            })
            assert wrong.status_code == 404
            found = await client.post("/api/reports/lookup", json={
                "public_code": payload["public_code"], "lookup_code": secret,
            })
            assert found.status_code == 200
            assert found.json()["description"] == body
            assert found.json()["reason"] == "illegal"
            alice = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
            use_access_token(client, alice)
            hidden = await client.get(f"/api/reports/{payload['public_code']}")
            assert hidden.status_code == 404
            assert body not in hidden.text
            assert (await client.get("/api/admin/reports")).status_code == 403
            admin = await client.post("/api/auth/login", json={"user_id": "admin", "password": "password-123"})
            use_access_token(client, admin)
            report_id = (await storage.report_by_code(payload["public_code"]))["id"]
            assert (await client.post(f"/api/admin/reports/{report_id}/accept")).status_code == 200
            noted = await client.post(
                f"/api/admin/reports/{report_id}/note", json={"note": "SECRET-NOTE"},
            )
            assert noted.status_code == 200
            resolved = await client.post(f"/api/admin/reports/{report_id}/resolve", json={
                "feedback": "已处理", "measure": "未删除消息、未封禁账号、未下架文档",
            })
            assert resolved.status_code == 200, resolved.text
            again = await client.post(f"/api/admin/reports/{report_id}/resolve", json={
                "feedback": "再次办结", "measure": "重复",
            })
            assert again.status_code == 409
            del client.headers["Authorization"]
            visitor = await client.post("/api/reports/lookup", json={
                "public_code": payload["public_code"], "lookup_code": secret,
            })
            assert visitor.status_code == 200
            assert visitor.json()["public_feedback"] == "已处理"
            assert "SECRET-NOTE" not in visitor.text
            assert "未删除消息" not in visitor.text
            audit = _audit_text(tmp_path)
            assert "查询投诉" in audit and "办结投诉" in audit
            assert all(value not in audit for value in (secret, body, contact, "SECRET-NOTE", "已处理"))

    asyncio.run(run())


def test_message_report_cannot_reference_another_user(monkeypatch):
    monkeypatch.setenv("profile", "")
    storage = MemoryReports()
    alice_conversation, alice_message = str(uuid4()), str(uuid4())
    bob_conversation, bob_message = str(uuid4()), str(uuid4())
    storage.add_message(
        owner="alice", conversation_id=alice_conversation, message_id=alice_message, content="ALICE-REPLY",
    )
    storage.add_message(
        owner="bob", conversation_id=bob_conversation, message_id=bob_message, content="BOB-PRIVATE",
    )

    async def run():
        transport = httpx.ASGITransport(_app(storage))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            logged = await client.post(
                "/api/auth/login", json={"user_id": "alice", "password": "password-123"},
            )
            use_access_token(client, logged)
            feedback = await client.post("/api/reports", json={
                "reason": "harmful",
                "description": "文档中的问题与出现时间",
                "target": {"kind": "general"},
            })
            assert feedback.status_code == 201, feedback.text
            general = await client.get(f"/api/reports/{feedback.json()['public_code']}")
            assert general.status_code == 200
            assert general.json()["target"]["kind"] == "general"
            assert general.json()["reason"] == "harmful"
            denied = await client.post("/api/reports", json={
                "reason": "harmful",
                "description": "越权举报",
                "target": {
                    "kind": "assistant_message",
                    "conversation_id": bob_conversation,
                    "message_id": bob_message,
                },
            })
            assert denied.status_code == 404
            assert "BOB-PRIVATE" not in denied.text
            created = await client.post("/api/reports", json={
                "reason": "harmful",
                "description": "举报自己的回复",
                "target": {
                    "kind": "assistant_message",
                    "conversation_id": alice_conversation,
                    "message_id": alice_message,
                },
            })
            assert created.status_code == 201, created.text
            mine = await client.get(f"/api/reports/{created.json()['public_code']}")
            assert mine.status_code == 200
            assert "ALICE-REPLY" not in mine.text

    asyncio.run(run())
