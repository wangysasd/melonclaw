"""投诉举报的数据库读写。服务层不直接写 SQL。"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from melonclaw.core.reports import (
    ReportFlowError,
    compact_excerpt,
    content_digest,
    new_public_code,
    transition_values,
)
from melonclaw.database.schema import chat_conversations, chat_messages, report_events, reports
from melonclaw.repository.conversations import visible_conversation_query
from melonclaw.repository.knowledge import documents as kb_documents
from melonclaw.repository.knowledge import revisions as kb_revisions
from melonclaw.repository.knowledge import texts as kb_texts
from melonclaw.repository.knowledge import visible_query
from melonclaw.repository.mappers import _now

logger = logging.getLogger(__name__)

_INSERT_FIELDS = (
    "kind", "reason", "description", "contact", "submitter_user_id", "lookup_code_hash",
    "conversation_id", "message_id", "message_seq", "message_created_at",
    "kb_id", "document_id", "revision_id", "revision_no", "source_hash",
    "target_label", "content_sha256", "excerpt", "char_count",
)


def _plain(row):
    if row is None:
        return None
    data = dict(row)
    for key, value in data.items():
        if isinstance(value, UUID):
            data[key] = str(value)
    return data


def _constraint_name(exc: IntegrityError) -> str:
    original = exc.orig
    # SQLAlchemy 的 asyncpg 适配器在 cause 上保留原始约束名。
    for error in (original, original.__cause__, getattr(original, "diag", None)):
        name = getattr(error, "constraint_name", None)
        if isinstance(name, str):
            return name
    return ""


class ReportRepositoryMixin:
    async def report_capture_message(self, user_id, conversation_id, message_id):
        if conversation_id is None or message_id is None:
            return None
        visible = visible_conversation_query(conversation_id, user_id).with_only_columns(
            chat_conversations.c.id,
        )
        query = (
            select(
                chat_messages.c.id,
                chat_messages.c.seq,
                chat_messages.c.created_at,
                chat_messages.c.content,
            )
            .where(
                chat_messages.c.id == message_id,
                chat_messages.c.conversation_id.in_(visible),
                chat_messages.c.role == "assistant",
                chat_messages.c.status == "completed",
                chat_messages.c.content != "",
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        content = row["content"] or ""
        return {
            "kind": "assistant_message",
            "conversation_id": str(conversation_id),
            "message_id": str(row["id"]),
            "message_seq": row["seq"],
            "message_created_at": row["created_at"],
            "content_sha256": content_digest(content),
            "excerpt": compact_excerpt(content),
            "char_count": len(content),
            "target_label": "助手回复",
        }

    async def report_capture_document(self, user_id, kb_id, document_id, revision_id):
        visible = visible_query(user_id).subquery()
        query = (
            select(
                kb_documents.c.display_name,
                kb_revisions.c.id,
                kb_revisions.c.revision_no,
                kb_revisions.c.source_hash,
                kb_texts.c.text_hash,
                func.char_length(kb_texts.c.content).label("char_count"),
                func.left(kb_texts.c.content, 400).label("prefix"),
            )
            .select_from(
                kb_documents.join(
                    kb_revisions,
                    and_(
                        kb_revisions.c.id == revision_id,
                        kb_revisions.c.document_id == kb_documents.c.id,
                        kb_revisions.c.kb_id == kb_documents.c.kb_id,
                    ),
                ).join(
                    kb_texts,
                    and_(
                        kb_texts.c.revision_id == kb_revisions.c.id,
                        kb_texts.c.kb_id == kb_revisions.c.kb_id,
                    ),
                )
            )
            .where(
                kb_documents.c.id == document_id,
                kb_documents.c.kb_id == kb_id,
                kb_documents.c.deleted_at.is_(None),
                kb_documents.c.kb_id.in_(select(visible.c.id)),
                kb_revisions.c.published_at.is_not(None),
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        return {
            "kind": "knowledge_document",
            "kb_id": str(kb_id),
            "document_id": str(document_id),
            "revision_id": str(row["id"]),
            "revision_no": row["revision_no"],
            "source_hash": row["source_hash"],
            "content_sha256": row["text_hash"],
            "excerpt": compact_excerpt(row["prefix"] or ""),
            "char_count": row["char_count"],
            "target_label": (row["display_name"] or "")[:255],
        }

    async def report_find_open(self, user_id, *, message_id=None, revision_id=None):
        if user_id is None or (message_id is None and revision_id is None):
            return None
        query = select(reports).where(
            reports.c.submitter_user_id == user_id,
            reports.c.status.in_(("pending", "processing")),
        )
        if message_id is not None:
            query = query.where(reports.c.message_id == message_id)
        else:
            query = query.where(reports.c.revision_id == revision_id)
        async with self.engine.connect() as connection:
            row = (await connection.execute(query.limit(1))).mappings().first()
        return _plain(row)

    async def report_insert(self, values: dict):
        for _attempt in range(5):
            now = _now()
            report_id = uuid4()
            payload = {key: values.get(key) for key in _INSERT_FIELDS}
            payload.update(
                id=report_id,
                public_code=new_public_code(),
                status="pending",
                target_label=payload.get("target_label") or "",
                excerpt=payload.get("excerpt") or "",
                created_at=now,
                updated_at=now,
            )
            try:
                async with self.engine.begin() as connection:
                    inserted = (
                        await connection.execute(insert(reports).values(**payload).returning(reports))
                    ).mappings().one()
                    await connection.execute(insert(report_events).values(
                        id=uuid4(),
                        report_id=report_id,
                        actor_user_id=payload["submitter_user_id"],
                        action="submitted",
                        internal_note=None,
                        public_feedback=None,
                        measure=None,
                        created_at=now,
                    ))
                return _plain(inserted)
            except IntegrityError as exc:
                name = _constraint_name(exc)
                if name.startswith("uq_reports_open"):
                    existing = await self.report_find_open(
                        payload["submitter_user_id"],
                        message_id=payload.get("message_id"),
                        revision_id=payload.get("revision_id"),
                    )
                    raise ReportFlowError(
                        "duplicate",
                        public_code=existing["public_code"] if existing else None,
                    ) from None
                if name == "uq_reports_public_code":
                    continue
                logger.warning("complaint insert rejected constraint=%s", name or "unknown")
                raise ReportFlowError(
                    "invalid", status=409, message="投诉提交失败，请稍后重试。"
                ) from None
        raise ReportFlowError("duplicate")

    async def report_by_code(self, public_code: str):
        query = select(reports).where(reports.c.public_code == public_code)
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _plain(row)

    async def report_by_id(self, report_id):
        query = select(reports).where(reports.c.id == report_id)
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _plain(row)

    async def report_list_user(self, user_id: str):
        query = (
            select(reports)
            .where(reports.c.submitter_user_id == user_id)
            .order_by(reports.c.created_at.desc(), reports.c.id.desc())
            .limit(100)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_plain(row) for row in rows]

    async def report_admin_list(self, *, status: str | None, public_code: str | None):
        query = select(reports).order_by(reports.c.updated_at.desc(), reports.c.id.desc()).limit(100)
        if status:
            query = query.where(reports.c.status == status)
        if public_code:
            query = query.where(reports.c.public_code == public_code)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_plain(row) for row in rows]

    async def report_events(self, report_id):
        query = (
            select(report_events)
            .where(report_events.c.report_id == report_id)
            .order_by(report_events.c.created_at, report_events.c.id)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_plain(row) for row in rows]

    async def report_apply(
        self, report_id, action, actor_id, *, note=None, feedback=None, measure=None,
    ):
        try:
            async with self.engine.begin() as connection:
                current = (
                    await connection.execute(
                        select(reports).where(reports.c.id == report_id).with_for_update()
                    )
                ).mappings().first()
                if current is None:
                    raise ReportFlowError("missing")
                updates, event = transition_values(
                    _plain(current), action, actor_id,
                    note=note, feedback=feedback, measure=measure, now=_now(),
                )
                updated = (
                    await connection.execute(
                        update(reports).where(reports.c.id == report_id).values(**updates).returning(reports)
                    )
                ).mappings().one()
                await connection.execute(insert(report_events).values(id=uuid4(), **event))
                return _plain(updated)
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_report_events_resolved":
                raise ReportFlowError("resolved") from None
            logger.warning(
                "complaint transition rejected constraint=%s",
                _constraint_name(exc) or "unknown",
            )
            raise ReportFlowError("state") from None

    async def report_message_liveness(self, conversation_id, message_id, expected_hash):
        row = await self._reported_message(conversation_id, message_id)
        if row is None:
            return {
                "available": False, "hash_matches": False, "status": None, "message_seq": None,
            }
        return {
            "available": True,
            "hash_matches": content_digest(row["content"] or "") == expected_hash,
            "status": row["status"],
            "message_seq": row["seq"],
        }

    async def report_read_message(self, conversation_id, message_id, expected_hash):
        row = await self._reported_message(conversation_id, message_id)
        if row is None or row["status"] != "completed":
            return None
        content = row["content"] or ""
        return content if content_digest(content) == expected_hash else None

    async def _reported_message(self, conversation_id, message_id):
        query = select(
            chat_messages.c.seq, chat_messages.c.status, chat_messages.c.content,
        ).select_from(
            chat_messages.join(chat_conversations, chat_messages.c.conversation_id == chat_conversations.c.id)
        ).where(
            chat_messages.c.id == message_id,
            chat_messages.c.conversation_id == conversation_id,
            chat_messages.c.role == "assistant",
            chat_conversations.c.status == "active",
        )
        async with self.engine.connect() as connection:
            return (await connection.execute(query)).mappings().first()

    async def report_revision_liveness(self, revision_id, expected_source, expected_text):
        query = (
            select(
                kb_revisions.c.revision_no,
                kb_revisions.c.source_hash,
                kb_revisions.c.published_at,
                kb_revisions.c.superseded_at,
                kb_texts.c.text_hash,
            )
            .select_from(
                kb_revisions.outerjoin(
                    kb_texts,
                    and_(
                        kb_texts.c.revision_id == kb_revisions.c.id,
                        kb_texts.c.kb_id == kb_revisions.c.kb_id,
                    ),
                )
            )
            .where(kb_revisions.c.id == revision_id)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return {
                "available": False,
                "superseded": False,
                "hash_matches": False,
                "revision_no": None,
                "published_at": None,
            }
        published = row["published_at"]
        return {
            "available": True,
            "superseded": row["superseded_at"] is not None,
            "hash_matches": (
                row["source_hash"] == expected_source and row["text_hash"] == expected_text
            ),
            "revision_no": row["revision_no"],
            "published_at": published.isoformat() if published is not None else None,
        }
