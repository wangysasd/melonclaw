"""完成正文的交付投影；与回复原子保存，只读查询不加载消息正文。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.schema import (
    chat_conversations,
    chat_messages,
    conversation_artifacts,
    projects,
)
from melonclaw.repository.mappers import _as_iso


def artifact_rows(message: Mapping[str, Any], refs: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {"conversation_id": message["conversation_id"],
         "ref_key": hashlib.sha256(json.dumps(ref, sort_keys=True).encode("utf-8")).hexdigest(),
         "ref": ref, "message_id": message["id"], "message_seq": message["seq"],
         "ordinal": ordinal, "created_at": message["created_at"]}
        for ordinal, ref in enumerate(refs)
    ]


async def upsert_artifacts(connection: AsyncConnection, rows: list[dict[str, Any]]) -> None:
    # 一批重建数据可能包含同一引用的多次交付；单条 INSERT 不得重复更新同一行。
    latest = {}
    for row in rows:
        key = (row["conversation_id"], row["ref_key"])
        if key not in latest or row["message_seq"] >= latest[key]["message_seq"]:
            latest[key] = row
    values = list(latest.values())
    for offset in range(0, len(values), 1000):
        statement = insert(conversation_artifacts).values(values[offset:offset + 1000])
        await connection.execute(statement.on_conflict_do_update(
            index_elements=[conversation_artifacts.c.conversation_id, conversation_artifacts.c.ref_key],
            set_={name: getattr(statement.excluded, name)
                  for name in ("ref", "message_id", "message_seq", "ordinal", "created_at")},
            where=statement.excluded.message_seq >= conversation_artifacts.c.message_seq,
        ))


class ArtifactRepositoryMixin:
    async def list_artifacts(self, conversation_id: UUID, user_id: str) -> list[dict[str, Any]]:
        query = (
            select(conversation_artifacts.c.ref, conversation_artifacts.c.message_id, conversation_artifacts.c.created_at)
            .select_from(conversation_artifacts.join(
                chat_conversations, conversation_artifacts.c.conversation_id == chat_conversations.c.id,
            ).outerjoin(projects, and_(
                chat_conversations.c.project_id == projects.c.id,
                chat_conversations.c.user_id == projects.c.user_id,
                projects.c.status == "active",
            )))
            .where(conversation_artifacts.c.conversation_id == conversation_id,
                   chat_conversations.c.user_id == user_id, chat_conversations.c.status == "active",
                   or_(chat_conversations.c.project_id.is_(None), projects.c.id.is_not(None)))
            .order_by(conversation_artifacts.c.message_seq.desc(), conversation_artifacts.c.ordinal)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [{"ref": row["ref"], "message_id": str(row["message_id"]),
                 "created_at": _as_iso(row["created_at"])} for row in rows]

    async def rebuild_artifacts(self, extract_refs: Callable[[str], list[dict[str, str]]]) -> int:
        """初始化时流式读取完成正文并原子重建；并发消息写入等待重建提交。"""
        query = select(
            chat_messages.c.id, chat_messages.c.conversation_id, chat_messages.c.seq,
            chat_messages.c.content, chat_messages.c.created_at,
        ).where(chat_messages.c.role == "assistant", chat_messages.c.status == "completed").order_by(chat_messages.c.seq)
        async with self.engine.begin() as connection:
            # 与完成回复的锁顺序一致：先锁消息，再锁交付投影。
            await connection.execute(text("LOCK TABLE chat_messages IN SHARE MODE"))
            await connection.execute(text("LOCK TABLE conversation_artifacts IN EXCLUSIVE MODE"))
            await connection.execute(delete(conversation_artifacts))
            async with connection.stream(query.execution_options(yield_per=100)) as result:
                async for batch in result.mappings().partitions(100):
                    rows = [item for message in batch for item in artifact_rows(message, extract_refs(message["content"]))]
                    await upsert_artifacts(connection, rows)
            return int(await connection.scalar(select(func.count()).select_from(conversation_artifacts)))
