"""投诉举报的内存仓储，供不依赖数据库的验收测试使用。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from melonclaw.core.passwords import hash_password
from melonclaw.core.reports import (
    ReportFlowError,
    compact_excerpt,
    content_digest,
    new_public_code,
    transition_values,
)
from melonclaw.repository.models import UserContext


def _now():
    return datetime.now(UTC)


class MemoryReports:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.events: list[dict] = []
        self.messages: dict[tuple, dict] = {}
        self.documents: dict[tuple, dict] = {}
        self.revisions: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.encoded = hash_password("password-123")
        self.users = {
            "admin": UserContext("admin", "管理员", "system", "系统", tenant_role="owner"),
            "alice": UserContext("alice", "爱丽丝", "team", "团队"),
            "bob": UserContext("bob", "鲍勃", "team", "团队"),
        }

    @asynccontextmanager
    async def account_guard(self, **_kwargs):
        yield

    async def active_user(self, user_id):
        return self.users.get(user_id)

    async def account_password(self, user_id):
        return self.encoded if user_id in self.users else None

    def add_message(self, *, owner, conversation_id, message_id, content, seq=2, status="completed"):
        self.messages[(owner, str(conversation_id), str(message_id))] = {
            "content": content, "seq": seq, "status": status, "created_at": _now(),
        }

    def add_document(self, *, user_id, kb_id, document_id, revision_id, text, revision_no=1, name="共享说明"):
        source = "a" * 64
        text_hash = content_digest(text)
        self.documents[(user_id, str(kb_id), str(document_id), str(revision_id))] = {
            "display_name": name,
            "revision_no": revision_no,
            "source_hash": source,
            "text_hash": text_hash,
            "text": text,
        }
        self.revisions[str(revision_id)] = {
            "revision_no": revision_no,
            "source_hash": source,
            "text_hash": text_hash,
            "text": text,
            "superseded": False,
            "published_at": _now(),
        }

    def revoke_document(self, user_id, kb_id, document_id, revision_id):
        self.documents.pop((user_id, str(kb_id), str(document_id), str(revision_id)), None)

    def publish_next(self, old_revision_id, new_revision_id, new_text):
        """新版本不改写被举报修订的留存文本。"""

        previous = self.revisions[str(old_revision_id)]
        previous["superseded"] = True
        self.revisions[str(new_revision_id)] = {
            "revision_no": previous["revision_no"] + 1,
            "source_hash": "b" * 64,
            "text_hash": content_digest(new_text),
            "text": new_text,
            "superseded": False,
            "published_at": _now(),
        }

    def _lock(self, report_id):
        key = str(report_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def report_capture_message(self, user_id, conversation_id, message_id):
        row = self.messages.get((user_id, str(conversation_id), str(message_id)))
        if row is None or row["status"] != "completed":
            return None
        content = row["content"]
        return {
            "kind": "assistant_message",
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "message_seq": row["seq"],
            "message_created_at": row["created_at"],
            "content_sha256": content_digest(content),
            "excerpt": compact_excerpt(content),
            "char_count": len(content),
            "target_label": "助手回复",
        }

    async def report_capture_document(self, user_id, kb_id, document_id, revision_id):
        row = self.documents.get((user_id, str(kb_id), str(document_id), str(revision_id)))
        if row is None:
            return None
        return {
            "kind": "knowledge_document",
            "kb_id": str(kb_id),
            "document_id": str(document_id),
            "revision_id": str(revision_id),
            "revision_no": row["revision_no"],
            "source_hash": row["source_hash"],
            "content_sha256": row["text_hash"],
            "excerpt": compact_excerpt(row["text"]),
            "char_count": len(row["text"]),
            "target_label": row["display_name"],
        }

    async def report_find_open(self, user_id, *, message_id=None, revision_id=None):
        if user_id is None:
            return None
        for row in self.rows.values():
            if row["submitter_user_id"] != user_id or row["status"] not in {"pending", "processing"}:
                continue
            if message_id is not None and row.get("message_id") == str(message_id):
                return row
            if revision_id is not None and row.get("revision_id") == str(revision_id):
                return row
        return None

    async def report_insert(self, values):
        now = _now()
        report_id = str(uuid4())
        existing = await self.report_find_open(
            values.get("submitter_user_id"),
            message_id=values.get("message_id"),
            revision_id=values.get("revision_id"),
        )
        if existing and (values.get("message_id") or values.get("revision_id")):
            raise ReportFlowError("duplicate", public_code=existing["public_code"])
        row = {key: values.get(key) for key in (
            "kind", "reason", "description", "contact", "submitter_user_id", "lookup_code_hash",
            "conversation_id", "message_id", "message_seq", "message_created_at",
            "kb_id", "document_id", "revision_id", "revision_no", "source_hash",
            "target_label", "content_sha256", "excerpt", "char_count",
        )}
        for key in ("conversation_id", "message_id", "kb_id", "document_id", "revision_id"):
            if row[key] is not None:
                row[key] = str(row[key])
        row.update(
            id=report_id, public_code=new_public_code(), status="pending",
            target_label=row.get("target_label") or "", excerpt=row.get("excerpt") or "",
            assignee_user_id=None, accepted_at=None, resolved_at=None,
            public_feedback=None, measure=None, created_at=now, updated_at=now,
        )
        self.rows[report_id] = row
        self.events.append({
            "id": str(uuid4()), "report_id": report_id, "actor_user_id": row["submitter_user_id"],
            "action": "submitted", "internal_note": None, "public_feedback": None,
            "measure": None, "created_at": now,
        })
        return row

    async def report_by_code(self, public_code):
        return next((row for row in self.rows.values() if row["public_code"] == public_code), None)

    async def report_by_id(self, report_id):
        return self.rows.get(str(report_id))

    async def report_list_user(self, user_id):
        return [row for row in self.rows.values() if row["submitter_user_id"] == user_id]

    async def report_admin_list(self, *, status, public_code):
        rows = list(self.rows.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        if public_code:
            rows = [row for row in rows if row["public_code"] == public_code]
        return rows

    async def report_events(self, report_id):
        key = str(report_id)
        return [event for event in self.events if event["report_id"] == key]

    async def report_apply(self, report_id, action, actor_id, *, note=None, feedback=None, measure=None):
        async with self._lock(report_id):
            row = self.rows.get(str(report_id))
            if row is None:
                raise ReportFlowError("missing")
            if action == "resolved" and any(
                event["report_id"] == str(report_id) and event["action"] == "resolved"
                for event in self.events
            ):
                raise ReportFlowError("resolved")
            updates, event = transition_values(
                row, action, actor_id, note=note, feedback=feedback, measure=measure, now=_now(),
            )
            row.update(updates)
            self.events.append({**event, "id": str(uuid4()), "report_id": str(report_id)})
            return row

    async def report_message_liveness(self, conversation_id, message_id, expected_hash):
        found = next((
            row for key, row in self.messages.items()
            if key[1] == str(conversation_id) and key[2] == str(message_id)
        ), None)
        if found is None:
            return {"available": False, "hash_matches": False, "status": None, "message_seq": None}
        return {
            "available": True,
            "hash_matches": content_digest(found["content"]) == expected_hash,
            "status": found["status"],
            "message_seq": found["seq"],
        }

    async def report_revision_liveness(self, revision_id, expected_source, expected_text):
        row = self.revisions.get(str(revision_id))
        if row is None:
            return {
                "available": False, "superseded": False, "hash_matches": False,
                "revision_no": None, "published_at": None,
            }
        return {
            "available": True,
            "superseded": row["superseded"],
            "hash_matches": row["source_hash"] == expected_source and row["text_hash"] == expected_text,
            "revision_no": row["revision_no"],
            "published_at": row["published_at"].isoformat(),
        }
