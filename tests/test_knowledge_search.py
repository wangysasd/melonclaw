"""检索编排、逐轮授权、无静默降级与工具输入边界。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pydantic import ValidationError

from melonclaw.core.knowledge import (
    KNOWLEDGE_TOOL_NAMES,
    KnowledgeDenied,
    KnowledgeError,
    consume_knowledge_search_budget,
)
from melonclaw.services.knowledge_search import (
    ChatKnowledgeProvider,
    KnowledgeSearchService,
    SearchQuery,
    keyword_terms,
    knowledge_turn_notice,
)
from melonclaw.tool.knowledge import build_knowledge_tools


def fixture():
    model, kb, doc, rev = (uuid4() for _ in range(4))
    rows = [dict(kb_id=kb, document_id=doc, revision_id=rev, chunk_id=uuid4(),
                 content="证据" * 3000, start_offset=100 * i, end_offset=100 * i + 6000,
                 title="年报", source_locator={"page": 1}, score=0.8) for i in range(8)]
    repo = SimpleNamespace(knowledge_detail=AsyncMock(return_value={"knowledge_mode": "vector", "embedding_model_id": model}),
        fixed_embedding_model_id=AsyncMock(return_value=model), knowledge_search=AsyncMock(return_value=rows),
        knowledge_query_validate=AsyncMock(side_effect=lambda user, base, raw: raw))
    service = KnowledgeSearchService(repo, SimpleNamespace(knowledge_embedding_dimensions=1024))
    return service, repo, kb, rows


def test_keyword_uses_no_embeddings_and_limits_content_and_document_share(monkeypatch):
    service, repo, kb, rows = fixture()
    build = Mock(side_effect=AssertionError("keyword must not build embeddings"))
    monkeypatch.setattr("melonclaw.services.knowledge_search.build_knowledge_embeddings", build)
    result = asyncio.run(service.search("alice", kb, {"query": "请问公司的营业收入", "mode": "keyword"}))
    assert len(result["items"]) == 1
    assert sum(len(row["content"]) for row in result["items"]) <= 24000
    assert all(len(row["content"]) <= 4000 for row in result["items"])
    assert all(str(kb) in row["source_url"] and str(rows[0]["revision_id"]) in row["source_url"] for row in result["items"])
    kwargs = repo.knowledge_search.call_args.kwargs
    assert kwargs["vector"] is None and kwargs["terms"] and kwargs["limit"] == 60
    build.assert_not_called()
    assert keyword_terms("请问 如何") == []
    punctuated = keyword_terms("C++ 和 C# 的营业收入")
    assert punctuated and all("+" not in term and "#" not in term for term in punctuated)


def embedding(monkeypatch, invoke):
    value = SimpleNamespace(aembed_query=invoke, http_client=SimpleNamespace(close=Mock()),
                            http_async_client=SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr("melonclaw.services.knowledge_search.build_knowledge_embeddings", lambda settings: value)
    return value


def test_vector_revocation_after_network_is_not_empty_success(monkeypatch):
    service, repo, kb, _ = fixture()
    async def embed(query):
        repo.knowledge_search.side_effect = KnowledgeDenied("知识库不存在或无权访问。")
        return [1.0] * 1024
    value = embedding(monkeypatch, embed)
    with pytest.raises(KnowledgeDenied):
        asyncio.run(service.search("alice", kb, {"query": "营业收入", "mode": "vector"}))
    value.http_client.close.assert_called_once()
    value.http_async_client.aclose.assert_awaited_once()


@pytest.mark.parametrize("response", [[0.0] * 1024, [1.0], [float("nan")] * 1024])
def test_vector_errors_do_not_fallback_to_keyword(monkeypatch, response):
    service, repo, kb, _ = fixture()
    value = embedding(monkeypatch, AsyncMock(return_value=response))
    with pytest.raises(KnowledgeError, match="向量检索服务暂不可用"):
        asyncio.run(service.search("alice", kb, {"query": "营业收入", "mode": "vector"}))
    repo.knowledge_search.assert_not_awaited()
    value.http_async_client.aclose.assert_awaited_once()


def test_concurrent_replacement_retries_once_and_drops_stale_rows():
    service, repo, kb, rows = fixture()
    repo.knowledge_query_validate.side_effect = [[], []]
    assert asyncio.run(service.search("alice", kb, {"query": "收入", "mode": "keyword"}))["items"] == []
    assert repo.knowledge_search.await_count == 2


def test_selection_is_per_context_not_provider_and_open_is_bounded():
    service, repo, kb, rows = fixture()
    provider = ChatKnowledgeProvider(SimpleNamespace(require_ready=lambda: repo, settings=service.settings))
    async def run():
        selected = SimpleNamespace(user_id="alice", knowledge_base_ids=(str(kb),))
        unselected = SimpleNamespace(user_id="alice", knowledge_base_ids=())
        assert await provider.selected(selected, kb) == str(kb)
        with pytest.raises(KnowledgeDenied, match="本轮"):
            await provider.search(unselected, kb, {"query": "收入"})
        with pytest.raises(KnowledgeError):
            await service.open_text("alice", kb, rows[0]["document_id"], limit=8001)
    asyncio.run(run())


def test_turn_notice_names_the_library_and_keeps_the_id():
    service, repo, kb, _ = fixture()
    repo.knowledge_detail = AsyncMock(return_value={"name": "年报\n库", "description": "公司年报\n和财务数据", "knowledge_mode": "vector"})
    described = asyncio.run(service.describe_selection("alice", [kb]))
    notice = knowledge_turn_notice(described)
    assert described == [{"id": str(kb), "name": "年报 库", "description": "公司年报 和财务数据", "mode_label": "向量知识库"}]
    assert "年报 库" in notice and str(kb) in notice and "不要先 find_tools" in notice
    assert "公司年报 和财务数据" in notice and "不可信数据" in notice
    assert "\n库" not in notice
    assert "未选择" in knowledge_turn_notice([])
    assert {tool.name for tool in build_knowledge_tools(SimpleNamespace())} == KNOWLEDGE_TOOL_NAMES


def test_knowledge_guidance_directs_relevant_multi_library_search():
    from melonclaw.core.prompts import KNOWLEDGE_GUIDANCE

    assert "分别检索相关的已选知识库" in KNOWLEDGE_GUIDANCE
    assert "不要因为某一个库已有结果" in KNOWLEDGE_GUIDANCE


def test_turn_notice_limits_description_and_hides_inaccessible_metadata():
    service, repo, kb, _ = fixture()
    repo.knowledge_detail = AsyncMock(return_value={"name": "资料", "description": "资料" * 200})
    described = asyncio.run(service.describe_selection("alice", [kb]))
    assert len(described[0]["description"]) == 300
    repo.knowledge_detail = AsyncMock(side_effect=KnowledgeDenied("无权访问"))
    described = asyncio.run(service.describe_selection("alice", [kb]))
    assert described[0]["description"] == ""
    assert "当前不可访问" in knowledge_turn_notice(described)


def test_tool_schemas_hide_identity_and_runtime_and_reject_spoofing():
    tools = build_knowledge_tools(SimpleNamespace())
    for tool in tools:
        schema = tool.tool_call_schema.model_json_schema()
        assert "runtime" not in schema.get("properties", {})
        if tool.name == "search_knowledge":
            assert "mode" not in schema.get("properties", {})
        if tool.name == "open_knowledge_document":
            assert "chunk_ids" in schema.get("properties", {})
        assert "user_id" not in schema.get("properties", {})
        assert "tenant_id" not in json.dumps(schema)
    with pytest.raises(ValidationError):
        SearchQuery.model_validate({"query": "收入", "filter": {"tenant_id": "other"}})
    with pytest.raises(ValidationError):
        SearchQuery.model_validate({"query": "   "})


def test_query_api_uses_cookie_identity_and_bounds_and_hides_internal_failures():
    import httpx
    from account_fixtures import authenticated_route_app

    from melonclaw.api.app import create_app

    async def run():
        service, repo, kb, rows = fixture()
        repo.knowledge_open_text = AsyncMock(return_value={"kb_id": kb, "document_id": rows[0]["document_id"],
            "revision_id": rows[0]["revision_id"], "title": "年报", "content": "收入42", "offset": 0,
            "next_offset": None, "total_chars": 4, "is_current": False, "revision_no": 1})
        app = authenticated_route_app(create_app(), "cookie-user")
        app.state.chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: repo, settings=service.settings))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            url = f"/api/knowledge-bases/{kb}"
            response = await client.post(url + "/search", json={"query": "收入", "mode": "keyword"})
            assert response.status_code == 200
            assert repo.knowledge_search.call_args.args[0] == "cookie-user"
            assert (await client.post(url + "/search", json={"query": "收入", "user_id": "other"})).status_code == 422
            text_url = url + f"/documents/{rows[0]['document_id']}/text"
            response = await client.get(text_url, params={"revision_id": str(rows[0]["revision_id"])})
            assert response.status_code == 200 and response.json()["is_current"] is False
            assert repo.knowledge_open_text.call_args.args[0] == "cookie-user"
            assert (await client.get(text_url, params={"limit": 8001})).status_code == 422
            repo.knowledge_search.side_effect = RuntimeError("SQL driver secret query")
            response = await client.post(url + "/search", json={"query": "收入", "mode": "keyword"})
            assert response.status_code == 503 and "secret" not in response.text
    asyncio.run(run())


def test_knowledge_api_logs_unexpected_error_type_only(caplog):
    from melonclaw.api.routes.knowledge import respond

    async def boom():
        raise RuntimeError("SQL driver secret query")

    caplog.set_level("WARNING", logger="melonclaw.api.routes.knowledge")
    response = asyncio.run(respond(boom()))
    assert response.status_code == 503
    assert b"secret" not in response.body
    assert "knowledge api failed error_type=RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_stopwords_only_query_is_explicit_and_still_checks_acl():
    service, repo, kb, _ = fixture()
    with pytest.raises(KnowledgeError, match="没有可检索的关键词"):
        asyncio.run(service.search("alice", kb, {"query": "请问 如何", "mode": "keyword"}))
    repo.knowledge_detail.assert_awaited_once()
    repo.knowledge_search.assert_not_awaited()
    repo.knowledge_detail.side_effect = KnowledgeDenied("知识库不存在或无权访问。")
    with pytest.raises(KnowledgeDenied):
        asyncio.run(service.search("alice", kb, {"query": "请问 如何", "mode": "keyword"}))


def test_keyword_selection_preserves_codes_years_full_terms_and_late_content():
    terms = keyword_terms("请分析贵州茅台2024年营业收入和净利润，并对比2023年的变化")
    assert len(terms) <= 8 and {"2024", "2023", "净利润", "贵州", "茅台"} <= set(terms)
    assert terms.index("净利润") < terms.index("净利")
    codes = keyword_terms("SH600519 在 2024 年的 revenue recognition policy 是什么")
    assert codes[:2] == ["2024", "sh600519"]
    assert {"revenue", "recognition", "policy"} <= set(codes)
    compound = keyword_terms("宁德时代新能源科技股份有限公司研发费用同比增长多少")
    assert {"科技股份", "有限公司", "新能源", "研发", "费用"} <= set(compound)
    assert "科技" not in compound or compound.index("科技股份") < compound.index("科技")
    assert len(keyword_terms("RWA资产代币化中智能合约和跨链桥的结算风险")) <= 8
    assert keyword_terms("，！！！") == []


def test_request_search_budget_is_concurrent_per_base_and_never_shared():
    async def run():
        counts = {}
        async def consume(kb_id):
            try:
                return consume_knowledge_search_budget(counts, kb_id)
            except KnowledgeError as exc:
                return exc
        first = await asyncio.gather(*(consume("kb-a") for _ in range(10)))
        second = await asyncio.gather(*(consume("kb-b") for _ in range(10)))
        assert sum(not isinstance(item, Exception) for item in first) == 5
        assert sum(not isinstance(item, Exception) for item in second) == 5
        assert sum(isinstance(item, KnowledgeError) for item in first) == 5
        separate = {}
        assert consume_knowledge_search_budget(separate, "kb-a")["kind"] == "initial"
        kinds = [item["kind"] for item in first if not isinstance(item, Exception)]
        assert kinds.count("initial") == 3 and kinds.count("supplementary") == 2
    asyncio.run(run())


def test_provider_enforces_budget_per_request_and_returns_quota():
    async def run():
        service, repo, kb, _ = fixture()
        provider = ChatKnowledgeProvider(SimpleNamespace(require_ready=lambda: repo, settings=service.settings))
        search = AsyncMock(return_value={"items": [], "mode": "hybrid"})
        provider.service = Mock(return_value=SimpleNamespace(search=search))
        first = SimpleNamespace(user_id="alice", knowledge_base_ids=(str(kb),))
        second = SimpleNamespace(user_id="bob", knowledge_base_ids=(str(kb),))
        for _ in range(5):
            result = await provider.search(first, kb, {"query": "收入"})
            assert result["query_budget"]["kind"] in {"initial", "supplementary"}
        with pytest.raises(KnowledgeError, match="2 次补检索"):
            await provider.search(first, kb, {"query": "收入"})
        assert (await provider.search(second, kb, {"query": "收入"}))["query_budget"]["kind"] == "initial"
        assert search.await_count == 6
    asyncio.run(run())


def test_context_read_merges_hits_binds_revision_and_cites_actual_range():
    async def run():
        kb, doc, rev = uuid4(), uuid4(), uuid4()
        text = "开头残句\n第一段落。\n命中片段正文。\n后续段落。\n末尾"
        first_start = text.index("命中")
        second_start = text.index("正文")
        hits = [
            {"chunk_id": uuid4(), "start_offset": first_start, "end_offset": first_start + 4,
             "total_chars": len(text)},
            {"chunk_id": uuid4(), "start_offset": second_start, "end_offset": second_start + 2,
             "total_chars": len(text)},
        ]
        async def open_text(_user, _kb, _doc, revision_id=None, *, offset=0, limit=0):
            return {"kb_id": kb, "document_id": doc, "revision_id": revision_id,
                "title": "年报", "revision_no": 2,
                "content": text[offset:offset + limit], "offset": offset,
                "next_offset": offset + limit if offset + limit < len(text) else None,
                "total_chars": len(text), "is_current": False}
        repo = SimpleNamespace(knowledge_context_chunks=AsyncMock(return_value=hits),
                               knowledge_open_text=AsyncMock(side_effect=open_text))
        service = KnowledgeSearchService(repo, SimpleNamespace())
        result = await service.open_context("alice", kb, doc, revision_id=rev,
            chunk_ids=[str(item["chunk_id"]) for item in hits], before_chars=first_start - 2,
            after_chars=len(text))
        assert len(result["items"]) == 1 and result["context"]["merged_ranges"] == 1
        item = result["items"][0]
        assert "开头残句" not in item["content"] and "第一段落" in item["content"]
        assert "命中片段正文" in item["content"]
        assert item["revision_id"] == rev and item["is_current"] is False
        query = parse_qs(urlparse(item["source_url"]).query)
        assert query["revision_id"] == [str(rev)]
        assert query["offset"] == [str(item["offset"])]
        assert query["limit"] == [str(len(item["content"]))]
        assert repo.knowledge_open_text.call_args.kwargs["revision_id"] == rev
        assert set(item["context_hit_ids"]) == {str(hit["chunk_id"]) for hit in hits}
    asyncio.run(run())


def test_context_read_enforces_total_budget_without_cutting_protected_hits():
    async def run():
        kb, doc, rev = uuid4(), uuid4(), uuid4()
        text = "".join("甲" * 2000 + "间" * 6000 for _ in range(4))[:-6000]
        hits = [{"chunk_id": uuid4(), "start_offset": index * 8000,
                 "end_offset": index * 8000 + 2000, "total_chars": len(text)} for index in range(4)]
        async def open_text(_user, _kb, _doc, revision_id=None, *, offset=0, limit=0):
            return {"kb_id": kb, "document_id": doc, "revision_id": revision_id, "title": "年报",
                "display_name": "年报", "revision_no": 1, "content": text[offset:offset + limit],
                "offset": offset, "next_offset": None, "total_chars": len(text), "is_current": True}
        repo = SimpleNamespace(knowledge_context_chunks=AsyncMock(return_value=hits),
                               knowledge_open_text=AsyncMock(side_effect=open_text))
        result = await KnowledgeSearchService(repo, SimpleNamespace()).open_context(
            "alice", kb, doc, revision_id=rev, chunk_ids=[hit["chunk_id"] for hit in hits],
            before_chars=1600, after_chars=4000)
        assert result["context"]["returned_chars"] == 16000
        assert result["context"]["truncated"] is True and len(result["items"]) == 3
        for index, item in enumerate(result["items"]):
            assert hits[index]["start_offset"] >= item["start_offset"]
            assert hits[index]["end_offset"] <= item["end_offset"]
    asyncio.run(run())


def test_context_read_requires_matching_visible_chunks():
    service, repo, kb, rows = fixture()
    repo.knowledge_context_chunks = AsyncMock(side_effect=KnowledgeDenied("无权"))
    with pytest.raises(KnowledgeDenied):
        asyncio.run(service.open_context("alice", kb, rows[0]["document_id"],
            revision_id=rows[0]["revision_id"], chunk_ids=[str(rows[0]["chunk_id"])]))
    with pytest.raises(KnowledgeError, match="版本"):
        asyncio.run(service.open_context("alice", kb, rows[0]["document_id"],
            revision_id=None, chunk_ids=[str(rows[0]["chunk_id"])]))
