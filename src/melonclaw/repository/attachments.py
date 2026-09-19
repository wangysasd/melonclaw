"""附件业务数据和消息关系的原子持久化操作。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import (
    chat_attachments,
    chat_conversations,
    chat_message_attachments,
    chat_messages,
)
from melonclaw.repository.errors import (
    AttachmentError,
    AttachmentInUseError,
    AttachmentNotFoundError,
    AttachmentQuotaError,
    AttachmentStateError,
    ConversationNotFoundError,
)
from melonclaw.repository.mappers import _as_iso, _conversation_title, _message_dict, _now
from melonclaw.repository.models import PreparedMessagePair


def _attachment_dict(row: Any) -> dict[str, Any]:
    """把数据库行转换为不含物理路径的附件元数据。"""

    return {
        "attachment_id": str(row["id"]),
        "file_name": str(row["original_name"]),
        "media_type": str(row["media_type"]),
        "kind": str(row["kind"]),
        "size_bytes": int(row["size_bytes"]),
        "derived_size_bytes": int(row["derived_size_bytes"]),
        "sha256": str(row["sha256"]),
        "status": str(row["status"]),
        "parse_status": str(row["parse_status"]),
        "parse_error_code": row["parse_error_code"],
        "expires_at": _as_iso(row["expires_at"]) if row["expires_at"] else None,
        "created_at": _as_iso(row["created_at"]),
    }


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "attachment_id",
            "file_name",
            "media_type",
            "kind",
            "size_bytes",
            "parse_status",
            "parse_error_code",
        )
        if key in row
    }


class AttachmentRepositoryMixin:
    """由 BusinessRepository 继承的附件 CRUD、claim 和绑定事务。"""

    async def create_attachment(
        self,
        *,
        attachment_id: UUID,
        user_id: str,
        project_id: UUID,
        original_name: str,
        media_type: str,
        kind: str,
        size_bytes: int,
        sha256: str,
        client_request_id: str | None,
        expires_at: datetime,
        project_max_bytes: int,
    ) -> dict[str, Any]:
        timestamp = _now()
        parse_status = "not_required" if kind == "image" else "pending"
        async with self.engine.begin() as connection:
            await self._lock_attachment_project(connection, project_id)
            total = await connection.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            chat_attachments.c.size_bytes
                            + chat_attachments.c.derived_size_bytes
                        ),
                        0,
                    )
                ).where(
                    and_(
                        chat_attachments.c.project_id == project_id,
                        chat_attachments.c.storage_purged_at.is_(None),
                    )
                )
            )
            if int(total or 0) + size_bytes > project_max_bytes:
                raise AttachmentQuotaError()
            try:
                result = await connection.execute(
                    insert(chat_attachments)
                    .values(
                        id=attachment_id,
                        user_id=user_id,
                        project_id=project_id,
                        original_name=original_name,
                        media_type=media_type,
                        kind=kind,
                        size_bytes=size_bytes,
                        derived_size_bytes=0,
                        sha256=sha256,
                        status="staged",
                        parse_status=parse_status,
                        parse_attempts=0,
                        client_request_id=client_request_id,
                        created_at=timestamp,
                        updated_at=timestamp,
                        expires_at=expires_at,
                    )
                    .returning(chat_attachments)
                )
            except IntegrityError:
                # 调用方会重新查询并按服务端指纹比较；这里不吞唯一约束冲突。
                raise
            row = result.mappings().first()
        if row is None:
            raise AttachmentError("附件登记失败。", "attachment_storage_error", 503)
        return _attachment_dict(row)

    async def find_attachment_upload(
        self,
        user_id: str,
        project_id: UUID,
        client_request_id: str,
    ) -> dict[str, Any] | None:
        query = select(chat_attachments).where(
            and_(
                chat_attachments.c.user_id == user_id,
                chat_attachments.c.project_id == project_id,
                chat_attachments.c.client_request_id == client_request_id,
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _attachment_dict(row) if row else None

    async def get_attachment(self, attachment_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(chat_attachments).where(chat_attachments.c.id == attachment_id)
                )
            ).mappings().first()
        return _attachment_dict(row) if row else None

    async def get_attachment_record(self, attachment_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(chat_attachments).where(chat_attachments.c.id == attachment_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def get_attachment_record_for_user(
        self,
        attachment_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(chat_attachments).where(
                        and_(
                            chat_attachments.c.id == attachment_id,
                            chat_attachments.c.user_id == user_id,
                            chat_attachments.c.status.not_in(["deleted", "expired"]),
                        )
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def get_attachment_for_user(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        conditions = [
            chat_attachments.c.id == attachment_id,
            chat_attachments.c.user_id == user_id,
            chat_attachments.c.status.not_in(["deleted", "expired"]),
        ]
        if project_id is not None:
            conditions.append(chat_attachments.c.project_id == project_id)
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(chat_attachments).where(and_(*conditions)))).mappings().first()
        return _attachment_dict(row) if row else None

    async def list_attachments_for_message(
        self,
        message_id: UUID,
    ) -> list[dict[str, Any]]:
        query = (
            select(chat_attachments, chat_message_attachments.c.ordinal)
            .join(
                chat_message_attachments,
                chat_message_attachments.c.attachment_id == chat_attachments.c.id,
            )
            .where(chat_message_attachments.c.message_id == message_id)
            .order_by(chat_message_attachments.c.ordinal.asc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_attachment_dict(row) for row in rows]

    async def attachment_records_for_message(self, message_id: UUID) -> list[dict[str, Any]]:
        query = (
            select(chat_attachments)
            .join(
                chat_message_attachments,
                chat_message_attachments.c.attachment_id == chat_attachments.c.id,
            )
            .where(chat_message_attachments.c.message_id == message_id)
            .order_by(chat_message_attachments.c.ordinal.asc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_attachment_parse_queue(self) -> list[dict[str, Any]]:
        query = select(chat_attachments.c.id.label("attachment_id")).where(
            chat_attachments.c.status == "staged",
            chat_attachments.c.parse_status.in_(["pending", "processing"]),
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_attachments_for_messages(
        self,
        message_ids: Sequence[UUID],
    ) -> dict[UUID, list[dict[str, Any]]]:
        if not message_ids:
            return {}
        query = (
            select(chat_message_attachments.c.message_id, chat_attachments)
            .join(
                chat_attachments,
                chat_attachments.c.id == chat_message_attachments.c.attachment_id,
            )
            .where(chat_message_attachments.c.message_id.in_(list(message_ids)))
            .order_by(
                chat_message_attachments.c.message_id,
                chat_message_attachments.c.ordinal,
            )
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        result: dict[UUID, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(UUID(str(row["message_id"])), []).append(_attachment_dict(row))
        return result

    async def delete_staged_attachment(
        self,
        attachment_id: UUID,
        user_id: str,
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                select(chat_attachments)
                .where(
                    and_(
                        chat_attachments.c.id == attachment_id,
                        chat_attachments.c.user_id == user_id,
                    )
                )
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None or row["status"] in {"deleted", "expired"}:
                raise AttachmentNotFoundError()
            if row["status"] != "staged":
                raise AttachmentInUseError()
            related = await connection.scalar(
                select(func.count())
                .select_from(chat_message_attachments)
                .where(chat_message_attachments.c.attachment_id == attachment_id)
            )
            if int(related or 0) > 0:
                raise AttachmentInUseError()
            updated = await connection.execute(
                update(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .values(status="deleted", updated_at=_now())
                .returning(chat_attachments)
            )
            row = updated.mappings().first()
        if row is None:
            raise AttachmentNotFoundError()
        return _attachment_dict(row)

    async def reset_attachment_parse(
        self,
        attachment_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        """把可重试的失败解析重置为 ``pending``，返回最新状态或 ``None``。

        - ``failed``：清空错误码与重试计数，等待后台重新 claim。
        - ``pending`` / ``processing``：已在队列里，原样返回，接口保持幂等。
        - ``processed`` / ``not_required``：无需重新解析，抛出业务错误。
        - ``deleted`` / ``expired`` / 非 staged：按不可用处理。
        """

        now = _now()
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(chat_attachments)
                    .where(
                        and_(
                            chat_attachments.c.id == attachment_id,
                            chat_attachments.c.user_id == user_id,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if row is None or row["status"] in {"deleted", "expired"}:
                return None
            if row["status"] != "staged":
                raise AttachmentInUseError()
            parse_status = row["parse_status"]
            if parse_status in {"processed", "not_required"}:
                raise AttachmentStateError(
                    "该附件无需重新解析。", "attachment_parse_not_retryable"
                )
            if parse_status == "failed":
                row = (
                    await connection.execute(
                        update(chat_attachments)
                        .where(chat_attachments.c.id == attachment_id)
                        .values(
                            parse_status="pending",
                            parse_error_code=None,
                            parse_worker_id=None,
                            parse_lease_expires_at=None,
                            parse_attempts=0,
                            updated_at=now,
                        )
                        .returning(chat_attachments)
                    )
                ).mappings().first()
        return _attachment_dict(row) if row else None

    async def mark_storage_purged(self, attachment_id: UUID) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                update(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .values(storage_purged_at=_now(), updated_at=_now())
            )

    async def claim_expired_attachments(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """以行锁抢占可清理的 staged 附件，并将其标记为 expired。"""

        now = _now()
        relation_exists = select(chat_message_attachments.c.attachment_id).where(
            chat_message_attachments.c.attachment_id == chat_attachments.c.id
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                select(chat_attachments)
                .where(
                    and_(
                        chat_attachments.c.status == "staged",
                        chat_attachments.c.expires_at.is_not(None),
                        chat_attachments.c.expires_at < now,
                        chat_attachments.c.storage_purged_at.is_(None),
                        ~relation_exists.exists(),
                    )
                )
                .order_by(chat_attachments.c.expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = result.mappings().all()
            if not rows:
                return []
            ids = [row["id"] for row in rows]
            await connection.execute(
                update(chat_attachments)
                .where(chat_attachments.c.id.in_(ids))
                .values(status="expired", updated_at=now)
            )
        return [dict(row) for row in rows]

    async def claim_attachment_parse(
        self,
        attachment_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any] | None:
        now = _now()
        async with self.engine.begin() as connection:
            result = await connection.execute(
                select(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None or row["status"] != "staged":
                return None
            eligible = row["parse_status"] == "pending" or (
                row["parse_status"] == "processing"
                and row["parse_lease_expires_at"] is not None
                and row["parse_lease_expires_at"] < now
            )
            if not eligible:
                return None
            attempts = int(row["parse_attempts"])
            if attempts >= max_attempts:
                await connection.execute(
                    update(chat_attachments)
                    .where(chat_attachments.c.id == attachment_id)
                    .values(
                        parse_status="failed",
                        parse_error_code="parse_stale",
                        parse_worker_id=None,
                        parse_lease_expires_at=None,
                        updated_at=now,
                    )
                )
                return None
            claimed = await connection.execute(
                update(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .values(
                    parse_status="processing",
                    parse_worker_id=worker_id,
                    parse_lease_expires_at=now + timedelta(seconds=lease_seconds),
                    parse_attempts=attempts + 1,
                    updated_at=now,
                )
                .returning(chat_attachments)
            )
            claimed_row = claimed.mappings().first()
        return _attachment_dict(claimed_row) if claimed_row else None

    async def complete_attachment_parse(
        self,
        attachment_id: UUID,
        worker_id: str,
        *,
        derived_size_bytes: int,
        project_max_bytes: int,
    ) -> bool:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                select(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None or row["status"] != "staged" or row["parse_status"] != "processing" or row["parse_worker_id"] != worker_id:
                return False
            await self._lock_attachment_project(connection, UUID(str(row["project_id"])))
            total = await connection.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            chat_attachments.c.size_bytes
                            + chat_attachments.c.derived_size_bytes
                        ),
                        0,
                    )
                ).where(
                    and_(
                        chat_attachments.c.project_id == row["project_id"],
                        chat_attachments.c.storage_purged_at.is_(None),
                    )
                )
            )
            current_derived = int(row["derived_size_bytes"])
            if int(total or 0) - current_derived + derived_size_bytes > project_max_bytes:
                await connection.execute(
                    update(chat_attachments)
                    .where(chat_attachments.c.id == attachment_id)
                    .values(
                        parse_status="failed",
                        parse_error_code="project_attachment_quota_exceeded",
                        parse_worker_id=None,
                        parse_lease_expires_at=None,
                        updated_at=_now(),
                    )
                )
                return False
            await connection.execute(
                update(chat_attachments)
                .where(chat_attachments.c.id == attachment_id)
                .values(
                    parse_status="processed",
                    parse_error_code=None,
                    parse_worker_id=None,
                    parse_lease_expires_at=None,
                    derived_size_bytes=derived_size_bytes,
                    updated_at=_now(),
                )
            )
        return True

    async def fail_attachment_parse(
        self,
        attachment_id: UUID,
        worker_id: str,
        error_code: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                update(chat_attachments)
                .where(
                    and_(
                        chat_attachments.c.id == attachment_id,
                        chat_attachments.c.parse_worker_id == worker_id,
                        chat_attachments.c.parse_status == "processing",
                    )
                )
                .values(
                    parse_status="failed",
                    parse_error_code=error_code[:80],
                    parse_worker_id=None,
                    parse_lease_expires_at=None,
                    updated_at=_now(),
                )
            )

    async def renew_attachment_parse_lease(
        self,
        attachment_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> bool:
        """在长解析期间续租；失去 claim 的 worker 不再延长旧租约。"""

        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(chat_attachments)
                .where(
                    and_(
                        chat_attachments.c.id == attachment_id,
                        chat_attachments.c.status == "staged",
                        chat_attachments.c.parse_status == "processing",
                        chat_attachments.c.parse_worker_id == worker_id,
                    )
                )
                .values(
                    parse_lease_expires_at=_now() + timedelta(seconds=lease_seconds),
                    updated_at=_now(),
                )
            )
        return result.rowcount == 1

    async def create_message_pair_with_attachments(
        self,
        conversation_id: UUID,
        user_id: str,
        project_id: UUID,
        request_id: str,
        content: str,
        *,
        attachment_ids: Sequence[UUID],
        model_id: str,
        model_provider: str,
        model_name: str,
        model_display_name: str,
        model_supports_image: bool,
        max_attachment_count: int,
        max_total_bytes: int,
        user_display_metadata: dict[str, Any] | None = None,
    ) -> PreparedMessagePair:
        if len(attachment_ids) > max_attachment_count:
            raise AttachmentStateError("单条消息附件数量超过限制。", "attachment_count_exceeded")
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentStateError("附件 ID 不能重复。", "duplicate_attachment_id")
        timestamp = _now()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        sorted_ids = sorted(attachment_ids, key=str)
        async with self.engine.begin() as connection:
            conversation = (
                await connection.execute(
                    select(chat_conversations)
                    .where(
                        and_(
                            chat_conversations.c.id == conversation_id,
                            chat_conversations.c.user_id == user_id,
                            chat_conversations.c.project_id == project_id,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if conversation is None:
                raise ConversationNotFoundError
            rows = []
            if sorted_ids:
                rows = (
                    await connection.execute(
                        select(chat_attachments)
                        .where(chat_attachments.c.id.in_(sorted_ids))
                        .order_by(chat_attachments.c.id)
                        .with_for_update()
                    )
                ).mappings().all()
            if len(rows) != len(sorted_ids):
                raise AttachmentNotFoundError()
            by_id = {UUID(str(row["id"])): row for row in rows}
            max_seq = await connection.scalar(
                select(func.coalesce(func.max(chat_messages.c.seq), 0)).where(
                    chat_messages.c.conversation_id == conversation_id
                )
            )
            first_seq = int(max_seq or 0) + 1
            total_bytes = 0
            for attachment_id in attachment_ids:
                row = by_id.get(attachment_id)
                if row is None or row["user_id"] != user_id or row["project_id"] != project_id:
                    raise AttachmentNotFoundError()
                if row["status"] not in {"staged", "attached"}:
                    raise AttachmentNotFoundError()
                if row["kind"] == "image":
                    if row["parse_status"] != "not_required":
                        raise AttachmentStateError("图片附件状态无效。", "attachment_parse_failed")
                    if not model_supports_image:
                        raise AttachmentStateError(
                            "当前模型不支持多模态图片输入。", "model_image_unsupported"
                        )
                elif row["parse_status"] != "processed":
                    if row["parse_status"] in {"pending", "processing"}:
                        raise AttachmentStateError(
                            "附件仍在解析，请稍候再发送。", "attachment_parsing_in_progress"
                        )
                    if row["parse_status"] == "failed":
                        raise AttachmentStateError(
                            "附件解析失败，请删除后重新上传。", "attachment_parse_failed"
                        )
                    raise AttachmentStateError("附件解析状态无效。", "attachment_parse_failed")
                total_bytes += int(row["size_bytes"])
            if total_bytes > max_total_bytes:
                raise AttachmentStateError("单条消息附件总大小超过限制。", "message_attachment_too_large")
            title_source = content.strip() or (
                str(by_id[attachment_ids[0]]["original_name"]) if attachment_ids else "附件消息"
            )
            title = _conversation_title(title_source) or "附件消息"
            await connection.execute(
                insert(chat_messages),
                [
                    {
                        "id": user_message_id,
                        "conversation_id": conversation_id,
                        "seq": first_seq,
                        "request_id": request_id,
                        "role": "user",
                        "content": content,
                        "status": "completed",
                        "display_metadata": user_display_metadata or {},
                        "error_code": None,
                        "model_id": None,
                        "model_provider": None,
                        "model_name": None,
                        "model_display_name": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                    {
                        "id": assistant_message_id,
                        "conversation_id": conversation_id,
                        "seq": first_seq + 1,
                        "request_id": request_id,
                        "role": "assistant",
                        "content": "",
                        "status": "pending",
                        "display_metadata": {},
                        "error_code": None,
                        "model_id": model_id,
                        "model_provider": model_provider,
                        "model_name": model_name,
                        "model_display_name": model_display_name,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                ],
            )
            if attachment_ids:
                await connection.execute(
                    insert(chat_message_attachments),
                    [
                        {
                            "message_id": user_message_id,
                            "attachment_id": attachment_id,
                            "ordinal": ordinal,
                            "created_at": timestamp,
                        }
                        for ordinal, attachment_id in enumerate(attachment_ids)
                    ],
                )
                await connection.execute(
                    update(chat_attachments)
                    .where(
                        and_(
                            chat_attachments.c.id.in_(list(attachment_ids)),
                            chat_attachments.c.status == "staged",
                        )
                    )
                    .values(status="attached", expires_at=None, updated_at=timestamp)
                )
            await connection.execute(
                update(chat_conversations)
                .where(chat_conversations.c.id == conversation_id)
                .values(title=title if conversation["title"] == "新会话" else conversation["title"], updated_at=timestamp)
            )
        # 读取刚写入的两行，避免在这里复制数据库映射逻辑。
        async with self.engine.connect() as connection:
            message_rows = (
                await connection.execute(
                    select(chat_messages)
                    .where(chat_messages.c.id.in_([user_message_id, assistant_message_id]))
                    .order_by(chat_messages.c.seq)
                )
            ).mappings().all()
        if len(message_rows) != 2:
            raise AttachmentError("消息创建后无法读取。", "message_storage_error", 503)
        user_row, assistant_row = message_rows
        summaries: tuple[dict[str, Any], ...] = ()
        if attachment_ids:
            summary_rows = await self.list_attachments_for_message(user_message_id)
            summaries = tuple(_summary(row) for row in summary_rows)
        return PreparedMessagePair(
            request_id,
            _message_dict(user_row),
            _message_dict(assistant_row),
            summaries,
        )

    async def _lock_attachment_project(self, connection: Any, project_id: UUID) -> None:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"melonclaw:attachments:{project_id}"},
        )
