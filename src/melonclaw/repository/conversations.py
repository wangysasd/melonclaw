"""Conversation 和消息仓储。"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, or_, select, update

from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.repository.errors import (
    AssistantStateConflictError,
    ConversationNotFoundError,
)
from melonclaw.repository.models import PreparedMessagePair, RequestRecord
from melonclaw.database.schema import chat_conversations, chat_messages, projects
from melonclaw.repository.mappers import (
    _as_iso,
    _conversation_dict,
    _conversation_title,
    _message_dict,
    _now,
    decode_conversation_cursor,
    encode_conversation_cursor,
)


class ConversationRepositoryMixin:
    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID,
    ) -> dict[str, Any]:
        conversation_id = uuid4()
        timestamp = _now()
        values = {
            "id": conversation_id,
            "user_id": user_id,
            "project_id": project_id,
            "title": "新会话",
            "agent_id": "quickstart-research-agent",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(chat_conversations).values(**values))
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise DatabaseSchemaError("刚创建的会话无法读取，请检查 Project 归属。")
        return conversation

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        query = (
            select(
                chat_conversations,
                projects.c.name.label("project_name"),
                projects.c.workdir_path,
            )
            .select_from(
                chat_conversations.join(
                    projects,
                    and_(
                        chat_conversations.c.project_id == projects.c.id,
                        chat_conversations.c.user_id == projects.c.user_id,
                    ),
                )
            )
            .where(
                and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _conversation_dict(row) if row else None

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        conditions = [
            chat_conversations.c.user_id == user_id,
        ]
        if project_id is not None:
            conditions.append(chat_conversations.c.project_id == project_id)
        if cursor:
            cursor_updated_at, cursor_id = decode_conversation_cursor(cursor)
            conditions.append(
                or_(
                    chat_conversations.c.updated_at < cursor_updated_at,
                    and_(
                        chat_conversations.c.updated_at == cursor_updated_at,
                        chat_conversations.c.id < cursor_id,
                    ),
                )
            )
        query = (
            select(
                chat_conversations,
                projects.c.name.label("project_name"),
                projects.c.workdir_path,
            )
            .select_from(
                chat_conversations.join(
                    projects,
                    and_(
                        chat_conversations.c.project_id == projects.c.id,
                        chat_conversations.c.user_id == projects.c.user_id,
                    ),
                )
            )
            .where(and_(*conditions))
            .order_by(
                chat_conversations.c.updated_at.desc(),
                chat_conversations.c.id.desc(),
            )
            .limit(limit + 1)
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = _conversation_dict(rows[-1])
            next_cursor = encode_conversation_cursor(last["updated_at"], last["id"])
        return [_conversation_dict(row) for row in rows], next_cursor

    async def list_messages(
        self,
        conversation_id: UUID,
        user_id: str,
        *,
        limit: int,
        before_seq: int | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int | None]:
        conversation = await self.get_conversation(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        conditions = [chat_messages.c.conversation_id == conversation_id]
        if before_seq is not None:
            if before_seq < 1:
                raise ValueError("before_seq 必须是正整数。")
            conditions.append(chat_messages.c.seq < before_seq)
        query = (
            select(chat_messages)
            .where(and_(*conditions))
            .order_by(chat_messages.c.seq.desc())
            .limit(limit + 1)
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        next_before_seq = int(rows[0]["seq"]) if has_more and rows else None
        return conversation, [_message_dict(row) for row in rows], next_before_seq

    async def find_request(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
    ) -> RequestRecord | None:
        conversation = await self.get_conversation(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError
        query = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.conversation_id == conversation_id,
                    chat_messages.c.request_id == request_id,
                )
            )
            .order_by(chat_messages.c.seq.asc())
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        if not rows:
            return None
        by_role = {str(row["role"]): _message_dict(row) for row in rows}
        user_message = by_role.get("user")
        assistant_message = by_role.get("assistant")
        if user_message is None or assistant_message is None:
            raise DatabaseSchemaError("请求消息对不完整，请检查 chat_messages 数据。")
        return RequestRecord(
            request_id=request_id,
            content=user_message["content"],
            user_message=user_message,
            assistant_message=assistant_message,
        )

    async def create_message_pair(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
        content: str,
        *,
        model_id: str,
        model_provider: str,
        model_name: str,
        model_display_name: str,
    ) -> PreparedMessagePair:
        timestamp = _now()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        async with self.engine.begin() as connection:
            conversation_query = select(chat_conversations).where(
                and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
            conversation = (await connection.execute(conversation_query)).mappings().first()
            if conversation is None:
                raise ConversationNotFoundError
            max_seq = await connection.scalar(
                select(func.coalesce(func.max(chat_messages.c.seq), 0)).where(
                    chat_messages.c.conversation_id == conversation_id
                )
            )
            first_seq = int(max_seq or 0) + 1
            title = (
                _conversation_title(content)
                if conversation["title"] == "新会话"
                else conversation["title"]
            )
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
                        "display_metadata": {},
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
            await connection.execute(
                update(chat_conversations)
                .where(chat_conversations.c.id == conversation_id)
                .values(title=title, updated_at=timestamp)
            )
        user_message = {
            "id": str(user_message_id),
            "conversation_id": str(conversation_id),
            "seq": first_seq,
            "request_id": request_id,
            "role": "user",
            "content": content,
            "status": "completed",
            "display_metadata": {},
            "error_code": None,
            "model": None,
            "created_at": _as_iso(timestamp),
            "updated_at": _as_iso(timestamp),
        }
        assistant_message = {
            **user_message,
            "id": str(assistant_message_id),
            "seq": first_seq + 1,
            "role": "assistant",
            "content": "",
            "status": "pending",
            "model": {
                "id": model_id,
                "display_name": model_display_name,
                "provider": model_provider,
                "model": model_name,
            },
        }
        return PreparedMessagePair(request_id, user_message, assistant_message)

    async def get_incomplete_assistant(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        if (
            await self.get_conversation(conversation_id, user_id)
            is None
        ):
            raise ConversationNotFoundError
        query = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.conversation_id == conversation_id,
                    chat_messages.c.role == "assistant",
                    chat_messages.c.status.in_(["pending", "interrupted"]),
                )
            )
            .order_by(chat_messages.c.seq.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _message_dict(row) if row else None

    async def update_assistant(
        self,
        conversation_id: UUID,
        assistant_message_id: UUID,
        *,
        content: str | None = None,
        status: str,
        display_metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        expected_status: str | Collection[str] | None = None,
    ) -> dict[str, Any]:
        """按预期状态 CAS 更新助手消息，防止旧执行覆盖终态。"""

        values: dict[str, Any] = {"status": status, "updated_at": _now()}
        if content is not None:
            values["content"] = content
        if display_metadata is not None:
            values["display_metadata"] = display_metadata
        values["error_code"] = error_code
        async with self.engine.begin() as connection:
            conditions = [
                chat_messages.c.id == assistant_message_id,
                chat_messages.c.conversation_id == conversation_id,
                chat_messages.c.role == "assistant",
            ]
            if expected_status is not None:
                statuses = (
                    [expected_status]
                    if isinstance(expected_status, str)
                    else list(expected_status)
                )
                if not statuses:
                    raise ValueError("expected_status 不能为空。")
                conditions.append(chat_messages.c.status.in_(statuses))
            result = await connection.execute(
                update(chat_messages)
                .where(and_(*conditions))
                .values(**values)
                .returning(chat_messages)
            )
            row = result.mappings().first()
            if row is None:
                current = await connection.execute(
                    select(chat_messages.c.status).where(
                        and_(
                            chat_messages.c.id == assistant_message_id,
                            chat_messages.c.conversation_id == conversation_id,
                            chat_messages.c.role == "assistant",
                        )
                    )
                )
                current_status = current.scalar()
                if current_status is None:
                    raise ConversationNotFoundError
                if expected_status is not None:
                    raise AssistantStateConflictError(
                        "助手消息状态已变化，旧执行不能覆盖当前状态。"
                    )
                raise ConversationNotFoundError
            await connection.execute(
                update(chat_conversations)
                .where(chat_conversations.c.id == conversation_id)
                .values(updated_at=values["updated_at"])
            )
        return _message_dict(row)
