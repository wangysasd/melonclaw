"""检索候选的最终授权和版本校验。"""
from sqlalchemy import select

from melonclaw.core.knowledge import KnowledgeError
from melonclaw.database.schema import KNOWLEDGE_TABLES
from melonclaw.repository.knowledge_search import _uuid

bases, members, documents, revisions, texts, chunks, jobs, events = KNOWLEDGE_TABLES


class KnowledgeQueryRepositoryMixin:
    async def knowledge_query_validate(self, user_id, kb_id, source_rows):
        kb_id = _uuid(kb_id, "知识库 ID")
        if any(_uuid(row["kb_id"], "知识库") != kb_id for row in source_rows):
            raise KnowledgeError("检索候选不属于当前知识库。")
        if len(source_rows) > 60:
            raise KnowledgeError("检索候选超过限制。")
        async with self.knowledge_transaction(user_id, kb_id) as (conn, _):
            ids = [_uuid(row["chunk_id"], "分块") for row in source_rows]
            valid = set((await conn.execute(select(chunks.c.id)
                .join(revisions, revisions.c.id == chunks.c.revision_id)
                .join(documents, documents.c.active_revision_id == revisions.c.id)
                .where(chunks.c.kb_id == kb_id, documents.c.kb_id == kb_id,
                       documents.c.deleted_at.is_(None), revisions.c.published_at.is_not(None),
                       chunks.c.id.in_(ids)))).scalars())
            return [row for row in source_rows if _uuid(row["chunk_id"], "分块") in valid]
