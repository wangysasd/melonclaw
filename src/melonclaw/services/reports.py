"""投诉举报用例。仓储负责 SQL，这里负责权限、限流和返回字段。"""

from __future__ import annotations

import logging
import re

from melonclaw.core.reports import (
    ANONYMOUS_NOTICE,
    CONTENT_REASONS,
    DUPLICATE_MESSAGE,
    GENERAL_REASONS,
    LOGGED_IN_NOTICE,
    LOOKUP_DENIED,
    RATE_MESSAGE,
    TARGET_DENIED,
    ReportFlowError,
    ReportRateLimiter,
    admin_summary,
    admin_view,
    clean_contact,
    clean_text,
    default_report_limiter,
    hash_lookup_code,
    lookup_matches,
    new_lookup_code,
    require_reason,
    submission_digest,
    user_summary,
    user_view,
)

logger = logging.getLogger(__name__)
_CODE = re.compile(r"^MC-[0-9A-F]{8}$")


def _trace(action: str) -> None:
    logger.info("complaint action=%s", action)


def _ip(value: str) -> str:
    text = (value or "").strip()
    return text or "unknown"


class ReportService:
    def __init__(self, storage, limiter: ReportRateLimiter | None = None):
        self.storage = storage
        self.limiter = limiter or default_report_limiter

    def _limit(self, key: str, limit: int, window: int) -> None:
        if not self.limiter.allow(key, limit, window):
            _trace("rate_limited")
            raise ReportFlowError("rate", status=429, message=RATE_MESSAGE)

    async def submit_anonymous(self, *, ip: str, reason: str, description: str, contact: str):
        address = _ip(ip)
        limiter = self.limiter
        chosen = require_reason(reason, GENERAL_REASONS)
        body = clean_text(description, 2000, "说明")
        phone_or_mail = clean_contact(contact)
        self._limit(f"anon-submit:{address}", limiter.submit_limit, limiter.submit_window)
        duplicate_key = f"dup:{submission_digest(address, chosen, body, phone_or_mail or '')}"
        if not limiter.allow(duplicate_key, 1, limiter.duplicate_window):
            _trace("duplicate")
            raise ReportFlowError("duplicate_recent", status=429, message=DUPLICATE_MESSAGE)
        secret = new_lookup_code()
        try:
            row = await self.storage.report_insert({
                "kind": "general",
                "reason": chosen,
                "description": body,
                "contact": phone_or_mail,
                "submitter_user_id": None,
                "lookup_code_hash": hash_lookup_code(secret),
                "target_label": "一般投诉",
                "excerpt": "",
            })
        except Exception:
            limiter.forget(duplicate_key)
            raise
        _trace("submitted")
        return {
            "public_code": row["public_code"],
            "lookup_code": secret,
            "status": row["status"],
            "notice": ANONYMOUS_NOTICE,
        }

    async def lookup(self, *, ip: str, public_code: str, lookup_code: str):
        address = _ip(ip)
        limiter = self.limiter
        if limiter.blocked(f"anon-fail:{address}", limiter.failure_limit, limiter.lookup_window):
            _trace("rate_limited")
            raise ReportFlowError("rate", status=429, message=RATE_MESSAGE)
        self._limit(f"anon-lookup:{address}", limiter.lookup_limit, limiter.lookup_window)
        code = public_code.strip().upper()
        presented = lookup_code.strip()
        row = await self.storage.report_by_code(code) if _CODE.fullmatch(code) else None
        stored = None
        if row is not None and row.get("submitter_user_id") is None:
            stored = row.get("lookup_code_hash")
        if not (8 <= len(presented) <= 80 and lookup_matches(stored, presented)):
            limiter.allow(f"anon-fail:{address}", limiter.failure_limit, limiter.lookup_window)
            _trace("lookup_denied")
            raise ReportFlowError("lookup", status=404, message=LOOKUP_DENIED)
        _trace("lookup_ok")
        return user_view(row, await self.storage.report_events(row["id"]))

    async def submit(self, user_id: str, *, reason: str, description: str, contact: str, target: dict):
        limiter = self.limiter
        self._limit(
            f"user-submit:{user_id}", limiter.user_submit_limit, limiter.user_submit_window,
        )
        body = clean_text(description, 2000, "说明")
        phone_or_mail = clean_contact(contact)
        values = await self._targeted(user_id, reason, target)
        values.update(description=body, contact=phone_or_mail, submitter_user_id=user_id)
        row = await self.storage.report_insert(values)
        _trace("submitted")
        return {
            "public_code": row["public_code"],
            "status": row["status"],
            "notice": LOGGED_IN_NOTICE,
        }

    async def _targeted(self, user_id: str, reason: str, target: dict) -> dict:
        kind = target.get("kind")
        if kind == "general":
            if any(target.get(key) for key in (
                "conversation_id", "message_id", "kb_id", "document_id", "revision_id",
            )):
                raise ReportFlowError("invalid", status=422, message="一般投诉不能附带举报对象。")
            return {
                "kind": "general",
                "reason": require_reason(reason, GENERAL_REASONS),
                "lookup_code_hash": None,
                "target_label": "一般投诉",
                "excerpt": "",
            }
        if kind == "assistant_message":
            snapshot = await self.storage.report_capture_message(
                user_id, target.get("conversation_id"), target.get("message_id"),
            )
        elif kind == "knowledge_document":
            snapshot = await self.storage.report_capture_document(
                user_id, target.get("kb_id"), target.get("document_id"), target.get("revision_id"),
            )
        else:
            raise ReportFlowError("invalid", status=422, message="请选择有效的举报对象。")
        if snapshot is None:
            _trace("target_denied")
            raise ReportFlowError("target", status=404, message=TARGET_DENIED)
        existing = await self.storage.report_find_open(
            user_id,
            message_id=snapshot.get("message_id"),
            revision_id=snapshot.get("revision_id"),
        )
        if existing is not None:
            raise ReportFlowError("duplicate", public_code=existing["public_code"])
        snapshot["reason"] = require_reason(reason, CONTENT_REASONS)
        snapshot["lookup_code_hash"] = None
        return snapshot

    async def list_mine(self, user_id: str):
        rows = await self.storage.report_list_user(user_id)
        return {"items": [user_summary(row) for row in rows]}

    async def get_mine(self, user_id: str, public_code: str):
        limiter = self.limiter
        self._limit(f"user-lookup:{user_id}", limiter.lookup_limit, limiter.lookup_window)
        code = public_code.strip().upper()
        row = await self.storage.report_by_code(code) if _CODE.fullmatch(code) else None
        if row is None or row.get("submitter_user_id") != user_id:
            _trace("lookup_denied")
            raise ReportFlowError("missing")
        return user_view(row, await self.storage.report_events(row["id"]))

    async def admin_list(self, *, status: str | None, public_code: str | None):
        code = public_code.strip().upper() if public_code else None
        if code and _CODE.fullmatch(code) is None:
            return {"items": []}
        rows = await self.storage.report_admin_list(status=status or None, public_code=code)
        return {"items": [admin_summary(row) for row in rows]}

    async def admin_get(self, report_id):
        row = await self.storage.report_by_id(report_id)
        if row is None:
            raise ReportFlowError("missing")
        return await self._detail(row)

    async def admin_read_message(self, report_id):
        row = await self.storage.report_by_id(report_id)
        if row is None or row["kind"] != "assistant_message":
            raise ReportFlowError("missing")
        content = await self.storage.report_read_message(
            row["conversation_id"], row["message_id"], row["content_sha256"],
        )
        if content is None:
            raise ReportFlowError("missing", status=404, message="举报时的助手回复已不可读取。")
        return {"content": content}

    async def accept(self, actor_id: str, report_id):
        detail = await self._detail(await self.storage.report_apply(report_id, "accepted", actor_id))
        _trace("accepted")
        return detail

    async def note(self, actor_id: str, report_id, note: str):
        text = clean_text(note, 2000, "内部备注")
        detail = await self._detail(
            await self.storage.report_apply(report_id, "noted", actor_id, note=text)
        )
        _trace("noted")
        return detail

    async def reply(self, actor_id: str, report_id, feedback: str):
        text = clean_text(feedback, 2000, "处理反馈")
        detail = await self._detail(
            await self.storage.report_apply(report_id, "replied", actor_id, feedback=text)
        )
        _trace("replied")
        return detail

    async def resolve(self, actor_id: str, report_id, feedback: str, measure: str):
        text = clean_text(feedback, 2000, "处理反馈")
        action = clean_text(measure, 500, "处理措施")
        detail = await self._detail(await self.storage.report_apply(
            report_id, "resolved", actor_id, feedback=text, measure=action,
        ))
        _trace("resolved")
        return detail

    async def _detail(self, row: dict):
        events = await self.storage.report_events(row["id"])
        message_live = None
        revision_live = None
        if row["kind"] == "assistant_message":
            message_live = await self.storage.report_message_liveness(
                row["conversation_id"], row["message_id"], row["content_sha256"],
            )
        elif row["kind"] == "knowledge_document":
            revision_live = await self.storage.report_revision_liveness(
                row["revision_id"], row["source_hash"], row["content_sha256"],
            )
        return admin_view(row, events, message_live, revision_live)
