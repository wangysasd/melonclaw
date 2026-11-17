"""检索范围、版本读取与权限边界只在显式隔离 PostgreSQL 库验证。"""

import asyncio
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import jieba
import pytest
from test_knowledge_ingestion_postgres import records, uploaded
from test_knowledge_management_postgres import create_base, isolated_repository

from melonclaw.core.knowledge import KnowledgeConflict, KnowledgeDenied, KnowledgeError
from melonclaw.services import knowledge_search as search_module
from melonclaw.services.knowledge_search import KnowledgeSearchService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要独立 test PostgreSQL 数据库及 pgvector",
)


async def publish(repo, base, content, *, document_id=None, search_text=None, vector=None):
    request = await uploaded(repo, base, actor="owner", doc_id=document_id)
    job = next(item for item in await repo.knowledge_queue() if item["id"] == request["id"])
    claimed = await repo.knowledge_claim(job)
    row = records(content)[0]
    row["search_text"] = search_text if search_text is not None else content
    if vector is not None:
        row["vector"] = json.dumps(vector)
    await repo.knowledge_publish(claimed, content, [row])
    await repo.knowledge_release_pipeline(claimed)
    return request


def test_search_is_scoped_to_acl_tenant_kb_and_embedding_space_with_or_terms():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            await repo.knowledge_member_save("owner", base["id"], "viewer", "viewer")
            first = await publish(
                repo,
                base,
                "alpha beta body",
                search_text="alpha beta",
                vector=[1.0] + [0.0] * 1023,
            )
            second = await publish(
                repo,
                base,
                "alpha body",
                search_text="alpha",
                vector=[0.5, 0.5] + [0.0] * 1022,
            )

            model_id = base["embedding_model_id"]
            vector_hits = await repo.knowledge_search(
                "viewer",
                base["id"],
                embedding_model_id=model_id,
                vector=[1.0] + [0.0] * 1023,
                limit=10,
            )
            assert [hit["document_id"] for hit in vector_hits] == [
                first["document_id"],
                second["document_id"],
            ]
            assert vector_hits[0]["title"] == "test.txt"
            assert vector_hits[0]["search_type"] == "vector"

            keyword_hits = await repo.knowledge_search(
                "viewer",
                base["id"],
                embedding_model_id=model_id,
                terms=["alpha", "beta"],
                limit=10,
            )
            assert [hit["document_id"] for hit in keyword_hits] == [
                first["document_id"],
                second["document_id"],
            ]
            assert [hit["matched_terms"] for hit in keyword_hits] == [2, 1]
            assert all(hit["search_type"] == "keyword" for hit in keyword_hits)

            narrowed = await repo.knowledge_search(
                "viewer",
                base["id"],
                embedding_model_id=model_id,
                terms=["alpha", "beta"],
                document_ids=[second["document_id"]],
            )
            assert [hit["document_id"] for hit in narrowed] == [second["document_id"]]
            assert (
                await repo.knowledge_search(
                    "viewer",
                    base["id"],
                    embedding_model_id=model_id,
                    terms=["alpha"],
                    document_ids=[uuid4()],
                )
                == []
            )

            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_search(
                    "viewer",
                    base["id"],
                    embedding_model_id=uuid4(),
                    vector=[1.0] + [0.0] * 1023,
                )
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_search(
                    "outsider",
                    base["id"],
                    embedding_model_id=model_id,
                    terms=["alpha"],
                )
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_search(
                    "target",
                    base["id"],
                    embedding_model_id=model_id,
                    terms=["alpha"],
                )

            other_base = await create_base(repo, owner="outsider", name="Other tenant")
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_search(
                    "viewer",
                    other_base["id"],
                    embedding_model_id=other_base["embedding_model_id"],
                    terms=["alpha"],
                )

            with pytest.raises(KnowledgeError):
                await repo.knowledge_search(
                    "viewer",
                    base["id"],
                    embedding_model_id=model_id,
                    terms=["alpha"],
                    document_ids=[uuid4()] * 101,
                )
            with pytest.raises(KnowledgeError):
                await repo.knowledge_search(
                    "viewer",
                    base["id"],
                    embedding_model_id=model_id,
                    vector=[float("nan")] + [0.0] * 1023,
                )

    asyncio.run(run())


def test_revalidation_rechecks_acl_current_revision_and_empty_results():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            await repo.knowledge_member_save("owner", base["id"], "viewer", "viewer")
            original = await publish(
                repo,
                base,
                "alpha old version",
                search_text="alpha",
            )
            candidates = await repo.knowledge_search(
                "viewer",
                base["id"],
                embedding_model_id=base["embedding_model_id"],
                terms=["alpha"],
            )
            assert (
                len(await repo.knowledge_query_validate("viewer", base["id"], candidates)) == 1
            )

            await publish(
                repo,
                base,
                "beta replacement",
                document_id=original["document_id"],
                search_text="beta",
            )
            assert await repo.knowledge_query_validate("viewer", base["id"], candidates) == []

            # Empty input still enters the transaction and reports revoked ACL.
            await repo.knowledge_member_save("owner", base["id"], "viewer", None)
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_query_validate("viewer", base["id"], [])
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_search(
                    "viewer", base["id"], embedding_model_id=base["embedding_model_id"], terms=[]
                )

    asyncio.run(run())


def test_open_text_limits_offsets_and_retained_published_history():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            await repo.knowledge_member_save("owner", base["id"], "viewer", "viewer")
            original_text = "甲乙🙂e\u0301\n旧版正文"
            original = await publish(repo, base, original_text, search_text="old")
            old_revision = original["revision_id"]
            old_doc = original["document_id"]
            old_hits = await repo.knowledge_search(
                "viewer", base["id"], embedding_model_id=base["embedding_model_id"], terms=["old"]
            )
            new_text = "新版本：甲乙🙂"
            replacement = await publish(
                repo, base, new_text, document_id=old_doc, search_text="new"
            )

            current = await repo.knowledge_open_text(
                "viewer", base["id"], old_doc, offset=1, limit=3
            )
            assert current["revision_id"] == replacement["revision_id"]
            assert current["content"] == new_text[1:4]
            assert current["offset"] == 1
            assert current["next_offset"] == 4
            assert current["is_current"] is True

            old = await repo.knowledge_open_text(
                "viewer", base["id"], old_doc, revision_id=old_revision, offset=0, limit=8000
            )
            assert old["content"] == original_text
            assert old["revision_no"] == 1
            assert old["is_current"] is False
            assert old["next_offset"] is None

            service = KnowledgeSearchService(repo, SimpleNamespace())
            context = await service.open_context(
                "viewer", base["id"], old_doc, revision_id=old_revision,
                chunk_ids=[old_hits[0]["chunk_id"]], before_chars=2, after_chars=3,
            )
            assert context["items"][0]["revision_id"] == old_revision
            assert context["items"][0]["is_current"] is False
            assert "旧版正文" in context["items"][0]["content"]
            assert parse_qs(urlparse(context["items"][0]["source_url"]).query)["revision_id"] == [str(old_revision)]

            beyond = await repo.knowledge_open_text(
                "owner", base["id"], old_doc, offset=1000, limit=1
            )
            assert beyond["content"] == ""
            assert beyond["offset"] == len(new_text)
            assert beyond["next_offset"] is None

            with pytest.raises(KnowledgeError):
                await repo.knowledge_open_text("owner", base["id"], old_doc, limit=8001)
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_open_text("outsider", base["id"], old_doc)
            with pytest.raises(KnowledgeDenied):
                await service.open_context(
                    "outsider", base["id"], old_doc, revision_id=old_revision,
                    chunk_ids=[old_hits[0]["chunk_id"]],
                )

            await repo.knowledge_document_action("owner", base["id"], old_doc, "delete")
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_open_text(
                    "owner", base["id"], old_doc, revision_id=old_revision
                )

    asyncio.run(run())


def test_search_service_repository_retriever_and_old_revision_round_trip(monkeypatch):
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            old_text = "人工智能旧版：" + "正文片段。" * 1100
            old_search = " ".join(jieba.cut_for_search(old_text, HMM=False))
            old = await publish(
                repo,
                base,
                old_text,
                search_text=old_search,
                vector=[1.0] + [0.0] * 1023,
            )
            settings = SimpleNamespace(
                knowledge_embedding_url="https://api.siliconflow.cn/v1",
                knowledge_embedding_model_name="BAAI/bge-m3",
                knowledge_embedding_dimensions=1024,
                knowledge_vector_min_score=0.4,
            )

            class HttpClient:
                def close(self):
                    pass

                async def aclose(self):
                    pass

            class TestEmbeddings:
                http_client = HttpClient()
                http_async_client = HttpClient()

                async def aembed_query(self, query):
                    assert query in {"人工智能 搜索", "请问人工智能如何分析", "人工智能 当前"}
                    return [1.0] + [0.0] * 1023

            monkeypatch.setattr(
                search_module, "build_knowledge_embeddings", lambda _settings: TestEmbeddings()
            )
            service = KnowledgeSearchService(repo, settings)
            original_search = repo.knowledge_search
            seen_vectors = []

            async def check_vector_is_list(*args, **kwargs):
                seen_vectors.append(kwargs["vector"])
                assert isinstance(kwargs["vector"], list)
                return await original_search(*args, **kwargs)

            repo.knowledge_search = check_vector_is_list
            vector_response = await service.search(
                "owner", base["id"], {"query": "人工智能 搜索", "mode": "vector", "top_k": 6}
            )
            repo.knowledge_search = original_search
            assert seen_vectors == [[1.0] + [0.0] * 1023]
            assert len(vector_response["items"]) == 1
            vector_item = vector_response["items"][0]
            assert vector_item["revision_id"] == old["revision_id"]
            assert len(vector_item["content"]) == 4000
            assert vector_item["snippet_end_offset"] == 4000
            assert vector_item["title"] == "test.txt"
            vector_url = urlparse(vector_item["source_url"])
            assert vector_url.path.endswith(f"/documents/{old['document_id']}/text")
            assert parse_qs(vector_url.query) == {
                "revision_id": [str(old["revision_id"])],
                "offset": ["0"],
                "limit": ["4000"],
            }

            new_text = "新版本人工智能：当前内容。"
            new = await publish(
                repo,
                base,
                new_text,
                document_id=old["document_id"],
                search_text=" ".join(jieba.cut_for_search(new_text, HMM=False)),
                vector=[1.0] + [0.0] * 1023,
            )
            old_open = await service.open_text(
                "owner",
                base["id"],
                old["document_id"],
                revision_id=old["revision_id"],
                offset=5,
                limit=60,
            )
            assert old_open["content"] == old_text[5:65]
            assert old_open["is_current"] is False
            assert parse_qs(urlparse(old_open["source_url"]).query) == {
                "revision_id": [str(old["revision_id"])],
                "offset": ["5"],
                "limit": ["60"],
            }
            current_open = await service.open_text(
                "owner", base["id"], old["document_id"], limit=8000
            )
            assert current_open["revision_id"] == new["revision_id"]
            assert current_open["content"] == new_text
            assert current_open["is_current"] is True

            keyword_response = await service.search(
                "owner", base["id"], {"query": "请问人工智能如何分析", "mode": "keyword"}
            )
            assert keyword_response["mode"] == "keyword"
            assert len(keyword_response["items"]) == 1
            keyword_item = keyword_response["items"][0]
            assert keyword_item["revision_id"] == new["revision_id"]
            assert keyword_item["matched_terms"] >= 1
            assert keyword_item["ranking"] == "keyword"

            hybrid_response = await service.search(
                "owner", base["id"], {"query": "人工智能 当前", "top_k": 6}
            )
            assert hybrid_response["mode"] == "hybrid" and hybrid_response["status"] == "ok"
            assert hybrid_response["branches"]["vector"]["candidates"] == 1
            assert hybrid_response["branches"]["keyword"]["candidates"] == 1
            assert hybrid_response["branches"]["fused"]["candidates"] == 1
            hybrid_item = hybrid_response["items"][0]
            assert hybrid_item["chunk_id"] == keyword_item["chunk_id"]
            assert hybrid_item["vector_rank"] == 1 and hybrid_item["keyword_rank"] == 1
            assert hybrid_item["ranking"] == "hybrid"

    asyncio.run(run())
