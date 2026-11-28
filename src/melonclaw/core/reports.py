"""投诉举报的纯规则：编号、摘要、限流和对外字段。不读数据库，不写日志正文。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Mapping
from datetime import datetime

CONTENT_REASONS = frozenset({
    "illegal", "harmful", "privacy", "infringement", "misinformation", "abuse", "other",
})
GENERAL_REASONS = CONTENT_REASONS | {"cannot_login"}
RATE_MESSAGE = "操作过于频繁，请稍后再试。"
DUPLICATE_MESSAGE = "请勿重复提交。如已提交，请使用保存的编号和查询码查询。"
LOOKUP_DENIED = "编号或查询码不正确。"
TARGET_DENIED = "无法举报这条内容。"
NOT_FOUND = "未找到该投诉举报。"
ANONYMOUS_NOTICE = (
    "请立即保存工单编号和查询码。查询码只显示这一次，离开页面后无法再查看。"
    "之后请凭编号和查询码在本页查询进度和反馈。"
)
LOGGED_IN_NOTICE = "已提交。请保存工单编号，可在「意见反馈」查看进度。"

_FLOW_HTTP = {
    "missing": (404, NOT_FOUND),
    "need_accept": (409, "请先受理后再继续处理。"),
    "accepted": (409, "该投诉已受理。"),
    "resolved": (409, "该投诉已办结。"),
    "state": (409, "该投诉当前不能进行此操作。"),
    "duplicate": (409, "请勿重复提交。"),
}


class ReportFlowError(Exception):
    """投诉流程拒绝。message 只使用固定文案，不携带正文、查询码或联系方式。"""

    def __init__(
        self,
        code: str,
        *,
        public_code: str | None = None,
        status: int | None = None,
        message: str | None = None,
    ):
        self.code = code
        self.public_code = public_code
        self.status = status
        self.message = message
        super().__init__(code)


def flow_response(exc: ReportFlowError) -> tuple[int, str]:
    if exc.status is not None and exc.message:
        return exc.status, exc.message
    status, message = _FLOW_HTTP[exc.code]
    if exc.code == "duplicate" and exc.public_code:
        message = f"你已提交过该内容的投诉，工单编号 {exc.public_code}。"
    return status, message


def report_channels(settings) -> dict:
    """只返回已经配置且能接收的联系渠道。没有邮箱时不展示姓名或响应时限。"""

    email = settings.report_contact_email
    if not email:
        return {}
    payload: dict = {"email": email}
    if settings.report_contact_name:
        payload["contact_name"] = settings.report_contact_name
    if settings.report_response_hours:
        payload["response_hours"] = settings.report_response_hours
    return payload


def compact_excerpt(value: str, limit: int = 160) -> str:
    return " ".join(value.split())[:limit]


def content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_lookup_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


_LOOKUP_DUMMY = hash_lookup_code("melonclaw-report-lookup-dummy")


def lookup_matches(stored: str | None, presented: str) -> bool:
    candidate = hash_lookup_code(presented)
    return hmac.compare_digest(stored or _LOOKUP_DUMMY, candidate) and bool(stored)


def new_lookup_code() -> str:
    return secrets.token_urlsafe(18)


def new_public_code() -> str:
    return "MC-" + secrets.token_hex(4).upper()


def submission_digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def clean_text(value: str, limit: int, label: str) -> str:
    text = value.strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ReportFlowError("invalid", status=422, message=f"{label}长度无效。")
    return text


def clean_contact(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if len(text) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ReportFlowError("invalid", status=422, message="联系方式格式无效。")
    return text


def require_reason(reason: str, allowed: frozenset[str]) -> str:
    if reason not in allowed:
        raise ReportFlowError("invalid", status=422, message="请选择有效的原因。")
    return reason


def plan_transition(status: str, action: str) -> str | None:
    """返回拒绝代码；允许继续时返回 None。"""

    if action == "accepted":
        if status == "pending":
            return None
        if status == "processing":
            return "accepted"
        return "resolved" if status == "resolved" else "state"
    if action in {"noted", "replied", "resolved"}:
        if status == "processing":
            return None
        if status == "pending":
            return "need_accept"
        if status == "resolved":
            return "resolved"
    return "state"


def transition_values(
    row: Mapping,
    action: str,
    actor_id: str,
    *,
    note: str | None,
    feedback: str | None,
    measure: str | None,
    now: datetime,
) -> tuple[dict, dict]:
    denied = plan_transition(str(row["status"]), action)
    if denied:
        raise ReportFlowError(denied)
    event = {
        "report_id": row["id"],
        "actor_user_id": actor_id,
        "action": action,
        "internal_note": note if action == "noted" else None,
        "public_feedback": feedback if action in {"replied", "resolved"} else None,
        "measure": measure if action == "resolved" else None,
        "created_at": now,
    }
    updates: dict = {"updated_at": now}
    if action == "accepted":
        updates.update(status="processing", assignee_user_id=actor_id, accepted_at=now)
    elif action == "replied":
        updates["public_feedback"] = feedback
    elif action == "resolved":
        updates.update(
            status="resolved",
            public_feedback=feedback,
            measure=measure,
            resolved_at=now,
        )
    return updates, event


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def user_target(row: Mapping) -> dict:
    kind = row["kind"]
    if kind == "assistant_message":
        return {"kind": kind, "label": "助手回复", "message_seq": row["message_seq"]}
    if kind == "knowledge_document":
        return {
            "kind": kind,
            "label": row["target_label"],
            "revision_no": row["revision_no"],
        }
    return {"kind": "general", "label": "一般投诉"}


def public_events(events) -> list[dict]:
    visible = []
    for event in events:
        action = event["action"]
        if action == "noted":
            continue
        item = {"action": action, "created_at": _iso(event["created_at"])}
        if action in {"replied", "resolved"}:
            item["feedback"] = event.get("public_feedback")
        visible.append(item)
    return visible


def user_view(row: Mapping, events) -> dict:
    """查询结果只含提交人自己的内容和公开反馈。"""

    return {
        "public_code": row["public_code"],
        "status": row["status"],
        "kind": row["kind"],
        "reason": row["reason"],
        "description": row["description"],
        "contact": row.get("contact"),
        "target": user_target(row),
        "public_feedback": row.get("public_feedback"),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "events": public_events(events),
    }


def user_summary(row: Mapping) -> dict:
    return {
        "public_code": row["public_code"],
        "status": row["status"],
        "kind": row["kind"],
        "reason": row["reason"],
        "target": user_target(row),
        "has_feedback": bool(row.get("public_feedback")),
        "updated_at": _iso(row["updated_at"]),
        "created_at": _iso(row["created_at"]),
    }


def admin_event(event: Mapping) -> dict:
    return {
        "action": event["action"],
        "created_at": _iso(event["created_at"]),
        "actor_user_id": event.get("actor_user_id"),
        "internal_note": event.get("internal_note"),
        "feedback": event.get("public_feedback"),
        "measure": event.get("measure"),
    }


def admin_target(row: Mapping, message_live, revision_live) -> dict:
    kind = row["kind"]
    target = {"kind": kind, "label": row.get("target_label") or "一般投诉"}
    if kind == "assistant_message":
        target.update({
            "conversation_id": row["conversation_id"],
            "message_id": row["message_id"],
            "message_seq": row["message_seq"],
            "message_created_at": _iso(row.get("message_created_at")),
            "char_count": row.get("char_count"),
            "excerpt": row.get("excerpt") or "",
            "live": message_live,
        })
    elif kind == "knowledge_document":
        target.update({
            "kb_id": row["kb_id"],
            "document_id": row["document_id"],
            "revision_id": row["revision_id"],
            "revision_no": row["revision_no"],
            "source_hash": row["source_hash"],
            "char_count": row.get("char_count"),
            "excerpt": row.get("excerpt") or "",
            "live": revision_live,
        })
    return target


def admin_view(row: Mapping, events, message_live=None, revision_live=None) -> dict:
    return {
        "id": row["id"],
        "public_code": row["public_code"],
        "status": row["status"],
        "kind": row["kind"],
        "reason": row["reason"],
        "description": row["description"],
        "contact": row.get("contact"),
        "submitter_user_id": row.get("submitter_user_id"),
        "assignee_user_id": row.get("assignee_user_id"),
        "public_feedback": row.get("public_feedback"),
        "measure": row.get("measure"),
        "target": admin_target(row, message_live, revision_live),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "accepted_at": _iso(row.get("accepted_at")),
        "resolved_at": _iso(row.get("resolved_at")),
        "events": [admin_event(event) for event in events],
    }


def admin_summary(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "public_code": row["public_code"],
        "status": row["status"],
        "kind": row["kind"],
        "reason": row["reason"],
        "target_label": row.get("target_label") or "一般投诉",
        "submitter_user_id": row.get("submitter_user_id"),
        "updated_at": _iso(row["updated_at"]),
        "created_at": _iso(row["created_at"]),
    }


class ReportRateLimiter:
    """单进程内存限流。匿名提交、匿名查询和登录用户提交分开计数。"""

    def __init__(
        self,
        *,
        submit_limit: int = 5,
        submit_window: int = 3600,
        lookup_limit: int = 20,
        lookup_window: int = 900,
        failure_limit: int = 8,
        duplicate_window: int = 600,
        user_submit_limit: int = 20,
        user_submit_window: int = 3600,
        clock=None,
    ):
        self.submit_limit = submit_limit
        self.submit_window = submit_window
        self.lookup_limit = lookup_limit
        self.lookup_window = lookup_window
        self.failure_limit = failure_limit
        self.duplicate_window = duplicate_window
        self.user_submit_limit = user_submit_limit
        self.user_submit_window = user_submit_window
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, list[float]]] = {}
        self._next_cleanup = 0.0

    def _prune(self, key: str, window: int, now: float) -> list[float]:
        entry = self._hits.get(key)
        return [item for item in entry[1] if now - item < window] if entry else []

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = self._clock()
        with self._lock:
            if now >= self._next_cleanup:
                self._hits = {
                    name: entry for name, entry in self._hits.items() if entry[0] > now
                }
                self._next_cleanup = now + 60
            if key not in self._hits and len(self._hits) >= 10_000:
                return False
            hits = self._prune(key, window, now)
            if len(hits) >= limit:
                return False
            hits.append(now)
            self._hits[key] = (now + window, hits)
            return True

    def blocked(self, key: str, limit: int, window: int) -> bool:
        now = self._clock()
        with self._lock:
            return len(self._prune(key, window, now)) >= limit

    def forget(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


default_report_limiter = ReportRateLimiter()
