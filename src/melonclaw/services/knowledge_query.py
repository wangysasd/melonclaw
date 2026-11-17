"""知识库单路/混合检索编排、RRF 融合、限长与最终授权校验。"""
import asyncio
import logging
from collections import Counter
from time import perf_counter
from uuid import UUID

from melonclaw.core.knowledge import KnowledgeDenied, KnowledgeError, KnowledgeUnavailable
from melonclaw.services.knowledge_search import (
    KnowledgeSearchService,
    SearchQuery,
    citation_markdown,
    keyword_terms,
    source_url,
)

logger = logging.getLogger(__name__)

CANDIDATE_LIMIT = 60
FUSED_CANDIDATE_LIMIT = 60
RRF_K = 60
BRANCH_TIMEOUT_SECONDS = 20
REQUEST_TIMEOUT_SECONDS = 45
SNIPPET_LIMIT = 4000
CONTENT_BUDGET = 24000


def _select_sources(rows, count, budget, *, single_document=False):
    output, per_doc = [], Counter()
    document_count = len({str(row["document_id"]) for row in rows})
    per_document_limit = count if single_document or document_count <= 1 else max(1, (count + 1) // 2)
    for row in rows:
        if len(output) >= count or budget <= 0:
            break
        if per_doc[str(row["document_id"])] >= per_document_limit:
            continue
        # 只在同一文档、同一版本内去重，不跨文档或跨版本合并正文与引用。
        if any(
            str(old["document_id"]) == str(row["document_id"])
            and str(old["revision_id"]) == str(row["revision_id"])
            and max(0, min(old["end_offset"], row["end_offset"]) - max(old["start_offset"], row["start_offset"]))
            >= 0.5 * min(old["end_offset"] - old["start_offset"], row["end_offset"] - row["start_offset"])
            for old in output
        ):
            continue
        value = {**row, "kind": "source_chunk", "ranking": row.get("search_type", "vector")}
        value["content"] = row["content"][:min(SNIPPET_LIMIT, budget)]
        value["snippet_end_offset"] = row["start_offset"] + len(value["content"])
        value["source_url"] = source_url(value, limit=max(1, len(value["content"])))
        value["citation"] = citation_markdown(value["title"], value["source_url"])
        budget -= len(value["content"])
        output.append(value)
        per_doc[str(row["document_id"])] += 1
    return output


def _fuse_rrf(vector_rows, keyword_rows, *, limit=FUSED_CANDIDATE_LIMIT):
    """仅融合排名；保留两路原始分数、排名和命中词数供对照。"""

    fused = {}
    for mode, rows in (("vector", vector_rows), ("keyword", keyword_rows)):
        for rank, row in enumerate(rows, start=1):
            key = str(row["chunk_id"])
            value = fused.setdefault(key, {**row, "rrf_score": 0.0})
            value["rrf_score"] += 1.0 / (RRF_K + rank)
            value[f"{mode}_rank"] = rank
            if row.get("score") is not None:
                value[f"{mode}_score"] = float(row["score"])
            if row.get("matched_terms") is not None:
                value["matched_terms"] = row["matched_terms"]
    for value in fused.values():
        value["score"] = value["rrf_score"]
        value["search_type"] = "hybrid"
    ordered = sorted(
        fused.values(),
        key=lambda row: (
            -row["rrf_score"],
            min(row.get("vector_rank", 10**9), row.get("keyword_rank", 10**9)),
            str(row["chunk_id"]),
        ),
    )
    return ordered[:limit]


def _branch_info(request, *, status="ok", error_code=None, candidates=0, returned=0,
                 below_threshold=0, duration_ms=0):
    return {
        "status": status,
        "error_code": error_code,
        "requested": request.top_k,
        "candidate_limit": CANDIDATE_LIMIT,
        "candidates": candidates,
        "returned": returned,
        "below_threshold": below_threshold,
        "duration_ms": duration_ms,
    }


class KnowledgeQueryService:
    def __init__(self, repository, settings):
        self.repository, self.settings = repository, settings

    async def _branch(self, service, user_id, kb_id, request, base, mode):
        started = perf_counter()
        branch_request = request.model_copy(update={"mode": mode})
        try:
            async with asyncio.timeout(BRANCH_TIMEOUT_SECONDS):
                rows = await service.candidates(user_id, kb_id, branch_request, base)
        except TimeoutError:
            label = "向量" if mode == "vector" else "关键词"
            error = KnowledgeUnavailable(f"{label}检索执行超时。", code=f"{mode}_timeout")
            error.duration_ms = round((perf_counter() - started) * 1000)
            raise error from None
        except KnowledgeUnavailable as exc:
            exc.duration_ms = round((perf_counter() - started) * 1000)
            raise
        info = _branch_info(request, candidates=len(rows), returned=len(rows),
                            duration_ms=round((perf_counter() - started) * 1000))
        return rows, info

    async def _hybrid(self, service, user_id, kb_id, request, base):
        started = perf_counter()
        vector_task = asyncio.create_task(self._branch(service, user_id, kb_id, request, base, "vector"))
        tasks = [vector_task]
        terms = keyword_terms(request.query)
        keyword_task = None
        if terms:
            keyword_task = asyncio.create_task(self._branch(service, user_id, kb_id, request, base, "keyword"))
            tasks.append(keyword_task)
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        vector_result = outcomes[0]
        keyword_result = outcomes[1] if keyword_task is not None else None
        if isinstance(vector_result, BaseException) and not isinstance(vector_result, KnowledgeUnavailable):
            raise vector_result
        if isinstance(keyword_result, asyncio.CancelledError):
            raise keyword_result
        if isinstance(keyword_result, BaseException):
            if isinstance(keyword_result, (KnowledgeDenied, KnowledgeError)) and not isinstance(
                keyword_result, KnowledgeUnavailable
            ):
                raise keyword_result
            if not isinstance(vector_result, BaseException):
                raise keyword_result
            raise KnowledgeUnavailable(
                "关键词与向量检索均执行失败，请稍后重试。", code="hybrid_failed"
            ) from None

        degraded = None
        vector_rows, vector_info = [], _branch_info(
            request, status="failed",
            duration_ms=getattr(vector_result, "duration_ms", round((perf_counter() - started) * 1000)),
        )
        if isinstance(vector_result, KnowledgeUnavailable):
            vector_info["error_code"] = vector_result.code
            degraded = {"branch": "vector", "reason": vector_result.code}
        else:
            vector_rows, vector_info = vector_result
            below = [row for row in vector_rows if row.get("score") is None
                     or float(row["score"]) < self.settings.knowledge_vector_min_score]
            vector_rows = [row for row in vector_rows if row.get("score") is not None
                           and float(row["score"]) >= self.settings.knowledge_vector_min_score]
            vector_info["below_threshold"] = len(below)
            vector_info["returned"] = len(vector_rows)

        if keyword_task is None:
            keyword_rows = []
            keyword_info = _branch_info(request, status="skipped", error_code="no_terms")
        else:
            keyword_rows, keyword_info = keyword_result
        if isinstance(vector_result, BaseException) and keyword_task is None:
            # 没有另一路可执行时不能伪装成空命中；正常关键词空命中仍可显式降级返回。
            raise vector_result

        rows = _fuse_rrf(vector_rows, keyword_rows)
        branches = {
            "vector": vector_info,
            "keyword": keyword_info,
            "fused": _branch_info(request, candidates=len(rows), returned=len(rows),
                                  duration_ms=round((perf_counter() - started) * 1000)),
        }
        return rows, branches, degraded

    async def search(self, user_id, kb_id, values):
        started = perf_counter()
        request = SearchQuery.model_validate(values)
        kb_id = UUID(str(kb_id))
        base = await self.repository.knowledge_detail(user_id, kb_id)
        service = KnowledgeSearchService(self.repository, self.settings)
        degraded = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                for attempt in range(2):
                    if request.mode == "hybrid":
                        rows, branches, degraded = await self._hybrid(service, user_id, kb_id, request, base)
                    else:
                        rows, branch = await self._branch(service, user_id, kb_id, request, base, request.mode)
                        below = 0
                        if request.mode == "vector":
                            kept = [row for row in rows if row.get("score") is not None
                                    and float(row["score"]) >= self.settings.knowledge_vector_min_score]
                            below = len(rows) - len(kept)
                            rows = kept
                        branch["below_threshold"] = below
                        branch["returned"] = len(rows)
                        branches = {request.mode: branch}
                    current = await self.repository.knowledge_query_validate(user_id, kb_id, rows)
                    if attempt == 0 and len(current) != len(rows):
                        continue
                    break
                items = _select_sources(
                    current, request.top_k, CONTENT_BUDGET,
                    single_document=len(request.document_ids) == 1,
                )
                branches["sources"] = _branch_info(
                    request, candidates=len(rows), returned=len(items),
                    below_threshold=branches.get("vector", {}).get("below_threshold", 0),
                    duration_ms=round((perf_counter() - started) * 1000),
                )
                response = {
                    "items": items,
                    "mode": request.mode,
                    "status": "degraded" if degraded else "ok",
                    "branches": branches,
                    "candidate_limit": CANDIDATE_LIMIT,
                    "fused_candidate_limit": FUSED_CANDIDATE_LIMIT if request.mode == "hybrid" else None,
                    "rrf_k": RRF_K if request.mode == "hybrid" else None,
                    "duration_ms": round((perf_counter() - started) * 1000),
                }
                if degraded:
                    response["degraded"] = degraded
                    response["notice"] = (
                        "本次向量检索超时，仅完成关键词检索；结果可能不完整。"
                        if degraded["reason"] == "vector_timeout" else
                        "本次向量检索服务不可用，仅完成关键词检索；结果可能不完整。"
                    )
        except TimeoutError:
            raise KnowledgeUnavailable("知识库检索超过整体时限。", code="search_timeout") from None
        logger.info(
            "knowledge search completed mode=%s status=%s vector_status=%s vector_candidates=%s "
            "keyword_status=%s keyword_candidates=%s fused_candidates=%s returned=%d duration_ms=%d",
            request.mode, response["status"],
            branches.get("vector", {}).get("status", "not_requested"),
            branches.get("vector", {}).get("candidates", 0),
            branches.get("keyword", {}).get("status", "not_requested"),
            branches.get("keyword", {}).get("candidates", 0),
            branches.get("fused", {}).get("candidates", 0), len(items), response["duration_ms"],
        )
        return response
