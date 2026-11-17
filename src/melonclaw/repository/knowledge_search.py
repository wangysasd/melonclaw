"""授权范围内的知识库检索与正文读取。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from melonclaw.core.knowledge import (
    KnowledgeConflict,
    KnowledgeDenied,
    KnowledgeError,
    require_role,
)

SEARCH_LIMIT_DEFAULT = 60
SEARCH_LIMIT_MAX = 60
DOCUMENT_FILTER_MAX = 100
VECTOR_DIMENSIONS = 1024
OPEN_TEXT_DEFAULT_LIMIT = 4000
OPEN_TEXT_MAX_LIMIT = 8000
OPEN_TEXT_MAX_OFFSET = 2_147_483_647
_KEYWORD_TERM = re.compile(r"^[\w.-]{1,64}$", re.UNICODE)


def _uuid(value, label: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise KnowledgeError(f"{label} 格式无效。") from exc


def _validate_search_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= SEARCH_LIMIT_MAX:
        raise KnowledgeError(f"检索条数必须为 1 到 {SEARCH_LIMIT_MAX}。")
    return limit


def _document_ids(values: Sequence[UUID | str] | None) -> tuple[UUID, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or len(values) > DOCUMENT_FILTER_MAX:
        raise KnowledgeError("文档筛选条件无效。")
    result = tuple(dict.fromkeys(_uuid(value, "文档 ID") for value in values))
    return result


def _vector_literal(values: Sequence[float] | None) -> str:
    if values is None or isinstance(values, (str, bytes)) or len(values) != VECTOR_DIMENSIONS:
        raise KnowledgeError(f"检索向量必须有 {VECTOR_DIMENSIONS} 个维度。")
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise KnowledgeError("检索向量必须由有限数值组成。")
    if not any(values):
        raise KnowledgeError("检索向量不能为零向量。")
    return json.dumps([float(value) for value in values], allow_nan=False, separators=(",", ":"))


def _tsquery(values: Sequence[str] | None) -> str | None:
    if values is None or isinstance(values, (str, bytes)) or len(values) > 8:
        raise KnowledgeError("关键词最多允许 8 个内容词。")
    terms = []
    for value in values:
        if not isinstance(value, str) or not _KEYWORD_TERM.fullmatch(value):
            raise KnowledgeError("关键词格式无效。")
        if value not in terms:
            terms.append(value)
    if not terms:
        return None
    # Each lexeme is quoted and the completed tsquery is still bound as a SQL value.
    return " | ".join("'" + term.replace("'", "''") + "':*" for term in terms)


def _tsquery_terms(values: Sequence[str]) -> tuple[str, ...]:
    combined = _tsquery(values)
    if combined is None:
        return ()
    return tuple("'" + term.replace("'", "''") + "':*" for term in dict.fromkeys(values))


def _query_scope_sql(base: str, ids: tuple[UUID, ...]) -> str:
    return base + (" AND v.document_id IN :document_ids" if ids else "")


class KnowledgeSearchRepositoryMixin:
    async def knowledge_search(
        self,
        user_id: str,
        kb_id: UUID | str,
        *,
        embedding_model_id: UUID | str,
        vector: Sequence[float] | None = None,
        terms: Sequence[str] | None = None,
        document_ids: Sequence[UUID | str] | None = None,
        limit: int = SEARCH_LIMIT_DEFAULT,
    ) -> list[dict]:
        """在单库、单嵌入空间中检索当前发布的原文片段。

        vector 与 terms 二选一。document_ids 只作为额外的收窄条件；所有者当前租户、
        知识库和当前有效版本由此仓储从已授权的数据库状态中构造，调用者无法覆盖。
        """

        kb_uuid = _uuid(kb_id, "知识库 ID")
        model_uuid = _uuid(embedding_model_id, "嵌入空间 ID")
        limit = _validate_search_limit(limit)
        ids = _document_ids(document_ids)
        if (vector is None) == (terms is None):
            raise KnowledgeError("必须且只能指定向量或关键词检索。")
        vector_value = _vector_literal(vector) if vector is not None else None
        query_terms = _tsquery(terms) if terms is not None else None
        async with self.knowledge_transaction(user_id, kb_uuid) as (conn, base):
            require_role(base["role"], "read")
            owner_tenant = await self._knowledge_owner_tenant(conn, base)
            if model_uuid != base["embedding_model_id"]:
                raise KnowledgeConflict("检索嵌入空间与知识库绑定不匹配。")

            if vector_value is not None:
                base_sql = """SELECT v.kb_id, v.document_id, v.revision_id, v.id AS chunk_id,
                    d.display_name AS title, v.content, v.start_offset, v.end_offset, v.source_locator,
                    (1 - (v.embedding <=> CAST(:query_vector AS vector)))::double precision AS score,
                    'vector'::text AS search_type
                FROM knowledge_search_chunks v
                JOIN knowledge_documents d ON d.kb_id=v.kb_id AND d.id=v.document_id
                WHERE v.tenant_id=:tenant_id AND v.kb_id=:kb_id
                  AND v.embedding_model_id=:embedding_model_id
                  AND v.document_id IS NOT NULL"""
                statement = text(
                    _query_scope_sql(base_sql, ids)
                    + " ORDER BY v.embedding <=> CAST(:query_vector AS vector), v.id LIMIT :row_limit"
                )
                params = {
                    "query_vector": vector_value,
                    "tenant_id": owner_tenant,
                    "kb_id": kb_uuid,
                    "embedding_model_id": model_uuid,
                    "row_limit": limit,
                }
            else:
                if query_terms is None:
                    return []
                matched_terms = _tsquery_terms(terms or ())
                hit_count_sql = " + ".join(
                    f"CASE WHEN v.search_vector @@ to_tsquery('pg_catalog.simple', :hit_{index}) THEN 1 ELSE 0 END"
                    for index in range(len(matched_terms))
                )
                base_sql = f"""WITH query AS (
                    SELECT to_tsquery('pg_catalog.simple', :keyword_query) AS q
                )
                SELECT v.kb_id, v.document_id, v.revision_id, v.id AS chunk_id,
                    d.display_name AS title, v.content, v.start_offset, v.end_offset, v.source_locator,
                    ({hit_count_sql})::integer AS matched_terms,
                    ts_rank_cd(v.search_vector, query.q)::double precision AS score,
                    'keyword'::text AS search_type
                FROM knowledge_search_chunks v
                JOIN knowledge_documents d ON d.kb_id=v.kb_id AND d.id=v.document_id
                CROSS JOIN query
                WHERE v.tenant_id=:tenant_id AND v.kb_id=:kb_id
                  AND v.embedding_model_id=:embedding_model_id
                  AND v.search_vector @@ query.q"""
                statement = text(
                    _query_scope_sql(base_sql, ids)
                    + " ORDER BY matched_terms DESC, score DESC, v.id LIMIT :row_limit"
                )
                params = {
                    "keyword_query": query_terms,
                    "tenant_id": owner_tenant,
                    "kb_id": kb_uuid,
                    "embedding_model_id": model_uuid,
                    "row_limit": limit,
                }
                params.update(
                    {f"hit_{index}": term_query for index, term_query in enumerate(matched_terms)}
                )
            if ids:
                statement = statement.bindparams(
                    bindparam("document_ids", expanding=True, type_=PGUUID(as_uuid=True))
                )
                params["document_ids"] = ids
            rows = (await conn.execute(statement, params)).mappings().all()
            return [dict(row) for row in rows]

    async def knowledge_context_chunks(
        self,
        user_id: str,
        kb_id: UUID | str,
        document_id: UUID | str,
        revision_id: UUID | str,
        chunk_ids: Sequence[UUID | str],
    ) -> list[dict]:
        """读取原命中的真实范围；文档、版本、权限和每个 chunk 必须完全一致。"""

        kb_uuid = _uuid(kb_id, "知识库 ID")
        document_uuid = _uuid(document_id, "文档 ID")
        revision_uuid = _uuid(revision_id, "版本 ID")
        if isinstance(chunk_ids, (str, bytes)) or not 1 <= len(chunk_ids) <= 4:
            raise KnowledgeError("上下文片段数量必须为 1 到 4。")
        ids = tuple(dict.fromkeys(_uuid(value, "分块 ID") for value in chunk_ids))
        statement = text("""SELECT c.id AS chunk_id, c.start_offset, c.end_offset,
                char_length(t.content) AS total_chars
            FROM knowledge_chunks c
            JOIN knowledge_document_revisions r ON r.kb_id=c.kb_id AND r.id=c.revision_id
            JOIN knowledge_documents d ON d.kb_id=c.kb_id AND d.id=c.document_id
                AND d.active_revision_id IS NOT NULL
            JOIN knowledge_document_texts t ON t.kb_id=r.kb_id AND t.revision_id=r.id
            WHERE c.kb_id=:kb_id AND c.document_id=:document_id AND c.revision_id=:revision_id
              AND d.deleted_at IS NULL AND r.published_at IS NOT NULL AND c.id IN :chunk_ids
            ORDER BY c.start_offset, c.end_offset, c.id""").bindparams(
            bindparam("chunk_ids", expanding=True, type_=PGUUID(as_uuid=True))
        )
        async with self.knowledge_transaction(user_id, kb_uuid) as (conn, base):
            require_role(base["role"], "read")
            rows = (await conn.execute(statement, {
                "kb_id": kb_uuid, "document_id": document_uuid,
                "revision_id": revision_uuid, "chunk_ids": ids,
            })).mappings().all()
        if len(rows) != len(ids):
            raise KnowledgeError("命中片段不存在、版本不匹配或已不可读取。")
        return [dict(row) for row in rows]

    async def knowledge_open_text(
        self,
        user_id: str,
        kb_id: UUID | str,
        document_id: UUID | str,
        revision_id: UUID | str | None = None,
        *,
        offset: int = 0,
        limit: int = OPEN_TEXT_DEFAULT_LIMIT,
    ) -> dict:
        """读取有权限文档的标准化正文片段，可显式打开仍保留的已发布旧版本。"""

        kb_uuid = _uuid(kb_id, "知识库 ID")
        document_uuid = _uuid(document_id, "文档 ID")
        revision_uuid = _uuid(revision_id, "版本 ID") if revision_id is not None else None
        if type(offset) is not int or not 0 <= offset <= OPEN_TEXT_MAX_OFFSET:
            raise KnowledgeError("正文偏移无效。")
        if type(limit) is not int or not 1 <= limit <= OPEN_TEXT_MAX_LIMIT:
            raise KnowledgeError(f"正文读取长度必须为 1 到 {OPEN_TEXT_MAX_LIMIT}。")

        async with self.knowledge_transaction(user_id, kb_uuid) as (conn, base):
            require_role(base["role"], "read")
            revision_clause = (
                "d.active_revision_id=r.id" if revision_uuid is None else "r.id=:revision_id"
            )
            query = text(f"""SELECT d.kb_id, d.id AS document_id, r.id AS revision_id,
                    d.display_name, r.revision_no, d.active_revision_id=r.id AS is_current,
                    char_length(t.content) AS total_chars,
                    substr(t.content, LEAST(:offset, char_length(t.content)) + 1, :limit) AS excerpt
                    FROM knowledge_documents d
                    JOIN knowledge_document_revisions r ON r.kb_id=d.kb_id AND r.document_id=d.id
                    JOIN knowledge_document_texts t ON t.kb_id=r.kb_id AND t.revision_id=r.id
                    WHERE d.kb_id=:kb_id AND d.id=:document_id AND d.deleted_at IS NULL
                      AND r.published_at IS NOT NULL AND {revision_clause}""")
            row = (
                (
                    await conn.execute(
                        query,
                        {
                            "kb_id": kb_uuid,
                            "document_id": document_uuid,
                            "offset": offset,
                            "limit": limit,
                            **({"revision_id": revision_uuid} if revision_uuid is not None else {}),
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KnowledgeDenied("文档版本不存在、已清理或无权访问。")
            total = int(row["total_chars"])
            start = min(offset, total)
            content = row["excerpt"] or ""
            next_offset = start + len(content)
            return {
                "kb_id": row["kb_id"],
                "document_id": row["document_id"],
                "revision_id": row["revision_id"],
                "title": row["display_name"],
                "revision_no": row["revision_no"],
                "content": content,
                "offset": start,
                "next_offset": next_offset if next_offset < total else None,
                "total_chars": total,
                "is_current": bool(row["is_current"]),
            }
