"""投诉举报的服务验收：匿名查询、消息归属、文档版本和办结幂等。"""

import asyncio
import json
import logging

import pytest
from report_memory import MemoryReports

from melonclaw.core.config import _REPORT_WARNED, load_settings
from melonclaw.core.reports import (
    CONTENT_REASONS,
    GENERAL_REASONS,
    ReportFlowError,
    ReportRateLimiter,
    flow_response,
    report_channels,
    user_view,
)
from melonclaw.database.constants import BUSINESS_TABLES
from melonclaw.database.schema import metadata
from melonclaw.database.updates import UPDATES
from melonclaw.services.reports import ReportService


def _service(storage, **limits):
    return ReportService(storage, limiter=ReportRateLimiter(**limits))


def test_channels_only_show_configured_responsible_contact(monkeypatch, caplog):
    monkeypatch.delenv("MELONCLAW_REPORT_CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("MELONCLAW_REPORT_CONTACT_NAME", raising=False)
    monkeypatch.delenv("MELONCLAW_REPORT_RESPONSE_HOURS", raising=False)
    _REPORT_WARNED.clear()
    assert report_channels(load_settings()) == {}
    monkeypatch.setenv("MELONCLAW_REPORT_RESPONSE_HOURS", "24")
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_NAME", "运营")
    assert report_channels(load_settings()) == {}
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_EMAIL", "not-an-email")
    with caplog.at_level(logging.WARNING):
        assert load_settings().report_contact_email == ""
    assert "not-an-email" not in caplog.text
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_EMAIL", "ops@example.com")
    _REPORT_WARNED.clear()
    assert report_channels(load_settings()) == {
        "email": "ops@example.com", "contact_name": "运营", "response_hours": 24,
    }


def test_report_tables_are_registered_for_init_and_upgrade():
    assert {"reports", "report_events"} <= set(BUSINESS_TABLES)
    assert {"reports", "report_events"} <= set(metadata.tables)
    names = {index.name for index in metadata.tables["report_events"].indexes}
    assert "uq_report_events_resolved" in names
    ids = [item.update_id for item in UPDATES]
    assert ids.index("20261126_reports") > ids.index("20261117_parser_model")


def test_reason_sets_keep_login_issues_off_content_reports():
    assert "cannot_login" in GENERAL_REASONS
    assert "cannot_login" not in CONTENT_REASONS


def test_user_view_hides_internal_note_measure_and_excerpt():
    row = {
        "public_code": "MC-ABCDEF01", "status": "resolved", "kind": "knowledge_document",
        "reason": "privacy", "description": "我提交的说明", "contact": None,
        "target_label": "制度", "revision_no": 2, "public_feedback": "已核查",
        "created_at": "2026-11-26T00:00:00+00:00", "updated_at": "2026-11-26T01:00:00+00:00",
        "excerpt": "OLD-DOC-SECRET", "measure": "SECRET-MEASURE", "lookup_code_hash": "abc",
        "source_hash": "a" * 64, "document_id": "DOC-ID-SENTINEL", "kb_id": "kb", "revision_id": "rev",
    }
    events = [
        {"action": "noted", "created_at": "2026-11-26T00:30:00+00:00", "internal_note": "SECRET-NOTE",
         "public_feedback": None, "measure": None},
        {"action": "resolved", "created_at": "2026-11-26T01:00:00+00:00", "internal_note": None,
         "public_feedback": "已核查", "measure": "SECRET-MEASURE"},
    ]
    payload = json.dumps(user_view(row, events), ensure_ascii=False)
    assert "已核查" in payload
    for secret in ("OLD-DOC-SECRET", "SECRET-MEASURE", "SECRET-NOTE", "abc", "DOC-ID-SENTINEL"):
        assert secret not in payload


def test_anonymous_round_trip_rejects_wrong_or_foreign_lookup(caplog):
    storage = MemoryReports()
    service = _service(storage, submit_limit=10, lookup_limit=30, failure_limit=20)

    async def run():
        with caplog.at_level(logging.INFO):
            created = await service.submit_anonymous(
                ip="203.0.113.8", reason="cannot_login", description="无法登录的说明", contact="",
            )
        secret = created["lookup_code"]
        assert secret not in json.dumps(storage.rows, default=str)
        assert secret not in caplog.text
        assert "无法登录的说明" not in caplog.text
        found = await service.lookup(ip="203.0.113.8", public_code=created["public_code"], lookup_code=secret)
        assert found["description"] == "无法登录的说明"
        assert found["status"] == "pending"
        other = await service.submit_anonymous(
            ip="203.0.113.9", reason="other", description="另一位访客的投诉", contact="guest@example.com",
        )
        for code, lookup in (
            (created["public_code"], "wrong-lookup-code"),
            ("MC-00000000", secret),
            (other["public_code"], secret),
        ):
            with pytest.raises(ReportFlowError) as caught:
                await service.lookup(ip="203.0.113.10", public_code=code, lookup_code=lookup)
            assert flow_response(caught.value) == (404, "编号或查询码不正确。")
        detail = await service.admin_get((await service.storage.report_by_code(created["public_code"]))["id"])
        assert "lookup_code" not in detail
        assert detail["contact"] is None

    asyncio.run(run())


def test_message_report_rejects_another_users_message_and_keeps_a_short_excerpt():
    storage = MemoryReports()
    service = _service(storage)
    body = "HEAD-MARKER " + ("x" * 400) + " TAIL-SECRET"
    storage.add_message(owner="alice", conversation_id="c-alice", message_id="m-alice", content=body)
    storage.add_message(owner="bob", conversation_id="c-bob", message_id="m-bob", content="BOB-PRIVATE")

    async def run():
        created = await service.submit("alice", reason="harmful", description="这条回复有问题", contact="", target={
            "kind": "assistant_message", "conversation_id": "c-alice", "message_id": "m-alice",
        })
        with pytest.raises(ReportFlowError) as caught:
            await service.submit("alice", reason="harmful", description="想看别人的回复", contact="", target={
                "kind": "assistant_message", "conversation_id": "c-bob", "message_id": "m-bob",
            })
        assert flow_response(caught.value)[0] == 404
        assert "BOB-PRIVATE" not in str(caught.value)
        detail = await service.admin_get((await storage.report_by_code(created["public_code"]))["id"])
        assert detail["target"]["message_id"] == "m-alice"
        assert detail["target"]["conversation_id"] == "c-alice"
        assert "HEAD-MARKER" in detail["target"]["excerpt"]
        assert "TAIL-SECRET" not in detail["target"]["excerpt"]
        assert detail["target"]["live"]["hash_matches"] is True
        mine = await service.get_mine("alice", created["public_code"])
        assert "HEAD-MARKER" not in json.dumps(mine)
        with pytest.raises(ReportFlowError) as missing:
            await service.get_mine("bob", created["public_code"])
        assert flow_response(missing.value)[0] == 404

    asyncio.run(run())


def test_document_report_keeps_the_reported_revision_after_access_is_revoked():
    storage = MemoryReports()
    service = _service(storage)
    storage.add_document(
        user_id="bob", kb_id="kb", document_id="doc", revision_id="rev-1", text="OLD-DOC-SECRET",
    )

    async def run():
        created = await service.submit("bob", reason="privacy", description="这份共享文档有问题", contact="", target={
            "kind": "knowledge_document", "kb_id": "kb", "document_id": "doc", "revision_id": "rev-1",
        })
        storage.publish_next("rev-1", "rev-2", "NEW-DOC-SECRET")
        storage.revoke_document("bob", "kb", "doc", "rev-1")
        with pytest.raises(ReportFlowError):
            await service.submit("bob", reason="privacy", description="权限撤销后不能再举报", contact="", target={
                "kind": "knowledge_document", "kb_id": "kb", "document_id": "doc", "revision_id": "rev-1",
            })
        detail = await service.admin_get((await storage.report_by_code(created["public_code"]))["id"])
        assert detail["target"]["revision_id"] == "rev-1"
        assert detail["target"]["revision_no"] == 1
        assert "OLD-DOC-SECRET" in detail["target"]["excerpt"]
        assert "NEW-DOC-SECRET" not in json.dumps(detail, ensure_ascii=False)
        assert detail["target"]["live"]["superseded"] is True
        assert detail["target"]["live"]["hash_matches"] is True
        mine = await service.get_mine("bob", created["public_code"])
        encoded = json.dumps(mine, ensure_ascii=False)
        assert mine["status"] == "pending"
        assert "OLD-DOC-SECRET" not in encoded
        assert "NEW-DOC-SECRET" not in encoded
        assert "rev-1" not in encoded

    asyncio.run(run())


def test_resolve_is_single_and_hides_the_internal_note(caplog):
    storage = MemoryReports()
    service = _service(storage)

    async def run():
        created = await service.submit_anonymous(
            ip="198.51.100.4", reason="abuse", description="需要处理的投诉", contact="caller@example.com",
        )
        row = await storage.report_by_code(created["public_code"])
        accepted = await service.accept("admin", row["id"])
        assert accepted["status"] == "processing"
        assert accepted["assignee_user_id"] == "admin"
        noted = await service.note("admin", row["id"], "SECRET-NOTE")
        assert noted["events"][-1]["internal_note"] == "SECRET-NOTE"
        replied = await service.reply("admin", row["id"], "我们已收到")
        assert replied["public_feedback"] == "我们已收到"
        with caplog.at_level(logging.INFO):
            results = await asyncio.gather(
                service.resolve("admin", row["id"], "已核查并反馈", "未删除消息、未封禁账号、未下架文档"),
                service.resolve("admin", row["id"], "第二次办结", "重复措施"),
                return_exceptions=True,
            )
        failures = [item for item in results if isinstance(item, ReportFlowError)]
        assert len([item for item in results if not isinstance(item, Exception)]) == 1
        assert len(failures) == 1
        assert flow_response(failures[0])[1] == "该投诉已办结。"
        assert sum(event["action"] == "resolved" for event in storage.events) == 1
        assert "caller@example.com" not in caplog.text
        assert "SECRET-NOTE" not in caplog.text
        visitor = await service.lookup(
            ip="198.51.100.4", public_code=created["public_code"], lookup_code=created["lookup_code"],
        )
        encoded = json.dumps(visitor, ensure_ascii=False)
        assert visitor["public_feedback"] == "已核查并反馈"
        assert "SECRET-NOTE" not in encoded
        assert "未删除消息" not in encoded
        assert storage.messages == {}

    asyncio.run(run())


def test_anonymous_submit_and_lookup_are_rate_limited():
    storage = MemoryReports()
    service = _service(storage, submit_limit=3, submit_window=3600, lookup_limit=20, failure_limit=2, lookup_window=900)

    async def run():
        first = await service.submit_anonymous(ip="203.0.113.20", reason="other", description="第一次", contact="")
        with pytest.raises(ReportFlowError) as duplicate:
            await service.submit_anonymous(ip="203.0.113.20", reason="other", description="第一次", contact="")
        assert flow_response(duplicate.value)[0] == 429
        await service.submit_anonymous(ip="203.0.113.20", reason="other", description="第二次", contact="")
        with pytest.raises(ReportFlowError) as limited:
            await service.submit_anonymous(ip="203.0.113.20", reason="other", description="第三次", contact="")
        assert flow_response(limited.value)[0] == 429
        for _ in range(2):
            with pytest.raises(ReportFlowError) as denied:
                await service.lookup(ip="203.0.113.20", public_code=first["public_code"], lookup_code="wrong-code-value")
            assert flow_response(denied.value)[0] == 404
        with pytest.raises(ReportFlowError) as locked:
            await service.lookup(ip="203.0.113.20", public_code=first["public_code"], lookup_code=first["lookup_code"])
        assert flow_response(locked.value)[0] == 429

    asyncio.run(run())
