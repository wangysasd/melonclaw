"""原文检索预算、去重、阈值与取消。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from melonclaw.core.knowledge import KnowledgeDenied, KnowledgeUnavailable
from melonclaw.services.knowledge_query import KnowledgeQueryService, _fuse_rrf, _select_sources


def test_sources_are_bounded_and_overlapping_results_are_deduplicated():
    kb, doc, rev = uuid4(), uuid4(), uuid4()
    row = dict(kb_id=kb, document_id=doc, revision_id=rev, chunk_id=uuid4(), title="原文",
        content="文" * 4000, start_offset=0, end_offset=4000, score=.9)
    rows = [row, {**row, "chunk_id": uuid4(), "start_offset": 100, "end_offset": 4100},
            {**row, "chunk_id": uuid4(), "revision_id": uuid4()}]
    selected = _select_sources(rows, 6, 24000)
    assert len(selected) == 2
    assert sum(len(item["content"]) for item in selected) <= 24000
    assert all(item["citation"] and item["source_url"] for item in selected)


def test_vector_threshold_and_final_revalidation(monkeypatch):
    kb, doc, rev = uuid4(), uuid4(), uuid4()
    row = dict(kb_id=kb, document_id=doc, revision_id=rev, chunk_id=uuid4(), title="原文",
        content="证据", start_offset=0, end_offset=2, score=.8)
    repo = SimpleNamespace(knowledge_detail=AsyncMock(return_value={}),
        knowledge_query_validate=AsyncMock(side_effect=lambda _u, _kb, rows: rows))
    async def candidates(*args):
        return [row, {**row, "chunk_id": uuid4(), "score": .2}]
    monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", candidates)
    result = asyncio.run(KnowledgeQueryService(repo, SimpleNamespace(knowledge_vector_min_score=.4)).search(
        "u", kb, {"query": "证据", "mode": "vector"}))
    assert [item["chunk_id"] for item in result["items"]] == [row["chunk_id"]]
    assert result["branches"]["sources"]["below_threshold"] == 1
    repo.knowledge_query_validate.assert_awaited_once()


def test_cancellation_waits_for_candidate_cleanup(monkeypatch):
    async def run():
        started, exited = asyncio.Event(), asyncio.Event()
        async def pending(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", pending)
        repo = SimpleNamespace(knowledge_detail=AsyncMock(return_value={}))
        task = asyncio.create_task(KnowledgeQueryService(repo, None).search("u", uuid4(), {"query": "文档"}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
    asyncio.run(run())


def result_row(*, document_id=None, revision_id=None, score=.8, start=0, content="证据"):
    kb = uuid4()
    doc = document_id or uuid4()
    rev = revision_id or uuid4()
    return dict(kb_id=kb, document_id=doc, revision_id=rev, chunk_id=uuid4(), title="原文",
                content=content, start_offset=start, end_offset=start + len(content), score=score)


def query_repo():
    return SimpleNamespace(knowledge_detail=AsyncMock(return_value={}),
        knowledge_query_validate=AsyncMock(side_effect=lambda _u, _kb, rows: rows))


def test_rrf_fuses_duplicate_chunks_keeps_branch_evidence_and_bounds_candidates():
    first, second, third = result_row(), result_row(), result_row()
    vector = [{**first, "score": .9}, {**second, "score": .8}]
    keyword = [{**second, "score": 2.0, "matched_terms": 2},
               {**third, "score": 1.0, "matched_terms": 1}]
    fused = _fuse_rrf(vector, keyword)
    assert [row["chunk_id"] for row in fused] == [second["chunk_id"], first["chunk_id"], third["chunk_id"]]
    assert fused[0]["vector_rank"] == 2 and fused[0]["keyword_rank"] == 1
    assert fused[0]["vector_score"] == .8 and fused[0]["keyword_score"] == 2.0
    assert fused[0]["matched_terms"] == 2 and fused[0]["score"] == fused[0]["rrf_score"]
    assert len(_fuse_rrf([result_row() for _ in range(60)], [result_row() for _ in range(60)])) == 60


def test_single_document_is_not_capped_to_half_but_cross_document_diversity_remains():
    doc = uuid4()
    rows = [result_row(document_id=doc, start=index * 100) for index in range(6)]
    assert len(_select_sources(rows, 6, 24000)) == 6
    other = [result_row(start=index * 100) for index in range(6)]
    selected = _select_sources(rows + other, 6, 24000)
    assert len(selected) == 6
    assert sum(item["document_id"] == doc for item in selected) == 3


def test_hybrid_recalls_in_parallel_and_returns_rrf_order(monkeypatch):
    async def run():
        first, second = result_row(), result_row()
        keyword_started = asyncio.Event()
        async def candidates(_self, _user, _kb, request, _base):
            if request.mode == "keyword":
                keyword_started.set()
                return [{**second, "score": 2.0, "matched_terms": 1}]
            await keyword_started.wait()
            return [first, second]
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", candidates)
        repo = query_repo()
        result = await asyncio.wait_for(KnowledgeQueryService(repo, SimpleNamespace(
            knowledge_vector_min_score=.4)).search("u", uuid4(), {"query": "证据"}), 1)
        assert result["status"] == "ok" and result["mode"] == "hybrid"
        assert result["items"][0]["chunk_id"] == second["chunk_id"]
        assert result["items"][0]["vector_rank"] == 2
        assert result["items"][0]["keyword_rank"] == 1
        assert result["branches"]["vector"]["candidates"] == 2
        assert result["branches"]["keyword"]["candidates"] == 1
        assert result["branches"]["fused"]["candidates"] == 2
        assert result["duration_ms"] >= 0
    asyncio.run(run())


def test_vector_timeout_explicitly_degrades_and_cleans_up(monkeypatch):
    async def run():
        row = result_row()
        exited = asyncio.Event()
        async def candidates(_self, _user, _kb, request, _base):
            if request.mode == "keyword":
                return [row]
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", candidates)
        monkeypatch.setattr("melonclaw.services.knowledge_query.BRANCH_TIMEOUT_SECONDS", .01)
        result = await KnowledgeQueryService(query_repo(), SimpleNamespace(
            knowledge_vector_min_score=.4)).search("u", uuid4(), {"query": "证据"})
        assert result["status"] == "degraded" and result["degraded"]["reason"] == "vector_timeout"
        assert "仅完成关键词检索" in result["notice"]
        assert result["branches"]["vector"]["status"] == "failed"
        assert result["branches"]["keyword"]["status"] == "ok"
        assert [item["chunk_id"] for item in result["items"]] == [row["chunk_id"]]
        assert exited.is_set()
    asyncio.run(run())


def test_hybrid_distinguishes_no_terms_empty_hits_and_execution_failures(monkeypatch):
    async def run():
        row = result_row()
        async def candidates(_self, _user, _kb, request, _base):
            if request.query == "有效词":
                return []
            if request.mode == "vector":
                return [row]
            return []
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", candidates)
        service = KnowledgeQueryService(query_repo(), SimpleNamespace(knowledge_vector_min_score=.4))
        no_terms = await service.search("u", uuid4(), {"query": "请问 如何"})
        assert no_terms["branches"]["keyword"]["status"] == "skipped"
        assert no_terms["branches"]["keyword"]["error_code"] == "no_terms"
        assert len(no_terms["items"]) == 1
        empty = await service.search("u", uuid4(), {"query": "有效词"})
        assert empty["status"] == "ok" and empty["items"] == []

        async def both_fail(_self, _user, _kb, request, _base):
            if request.mode == "vector":
                raise KnowledgeUnavailable("向量失败", code="vector_unavailable")
            raise RuntimeError("database secret")
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", both_fail)
        with pytest.raises(KnowledgeUnavailable, match="均执行失败") as caught:
            await service.search("u", uuid4(), {"query": "证据"})
        assert caught.value.code == "hybrid_failed"

        async def keyword_fail(_self, _user, _kb, request, _base):
            if request.mode == "vector":
                return [row]
            raise RuntimeError("keyword failed")
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", keyword_fail)
        with pytest.raises(RuntimeError, match="keyword failed"):
            await service.search("u", uuid4(), {"query": "证据"})

        async def cancelled(_self, _user, _kb, request, _base):
            if request.mode == "vector":
                raise KnowledgeUnavailable("向量失败", code="vector_unavailable")
            raise asyncio.CancelledError
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await service.search("u", uuid4(), {"query": "证据"})
    asyncio.run(run())


def test_permission_failure_never_degrades_and_overall_timeout_cleans_both(monkeypatch):
    async def run():
        async def denied(_self, _user, _kb, request, _base):
            if request.mode == "vector":
                raise KnowledgeUnavailable("向量失败", code="vector_unavailable")
            raise KnowledgeDenied("无权")
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", denied)
        service = KnowledgeQueryService(query_repo(), SimpleNamespace(knowledge_vector_min_score=.4))
        with pytest.raises(KnowledgeDenied):
            await service.search("u", uuid4(), {"query": "证据"})

        started, exited = asyncio.Event(), set()
        async def pending(_self, _user, _kb, request, _base):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.add(request.mode)
        monkeypatch.setattr("melonclaw.services.knowledge_query.KnowledgeSearchService.candidates", pending)
        monkeypatch.setattr("melonclaw.services.knowledge_query.REQUEST_TIMEOUT_SECONDS", .01)
        with pytest.raises(KnowledgeUnavailable) as caught:
            await service.search("u", uuid4(), {"query": "证据"})
        assert caught.value.code == "search_timeout" and exited == {"vector", "keyword"}
        assert started.is_set()
    asyncio.run(run())
