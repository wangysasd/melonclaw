"""整份 OCR 结果经模拟识别后进入真实发布、关键词检索和版本原文回读。"""
import asyncio
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.datastructures import UploadFile
from test_knowledge_management_postgres import create_base, isolated_repository

from melonclaw.core.config import Settings
from melonclaw.core.knowledge_ocr import OcrPageResult
from melonclaw.services.knowledge_documents import KnowledgeDocuments
from melonclaw.services.knowledge_processing import KnowledgeProcessor
from melonclaw.services.knowledge_search import KnowledgeSearchService

pytestmark = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离test PostgreSQL与pgvector")


def test_ocr_upload_publish_search_and_version_readback(tmp_path, monkeypatch):
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            settings = Settings(data_root=tmp_path, workspace_root=tmp_path / "workspace",
                                knowledge_embedding_api_key="test-only", knowledge_embedding_url="https://api.siliconflow.cn/v1",
                                knowledge_embedding_model_name="BAAI/bge-m3", knowledge_embedding_dimensions=1024)
            original = b"PK\x03\x04 office fixture"
            uploaded = await KnowledgeDocuments(settings, repo).upload("owner", base["id"],
                UploadFile(io.BytesIO(original), filename="Office验证.docx"), str(uuid4()))
            job = await repo.knowledge_claim((await repo.knowledge_queue())[0])

            async def recognize(_settings, content, extension):
                assert extension == ".docx" and content == original
                return [OcrPageResult(page=1, raw="收益 123.45", text="收益 123.45", finish_reason="stop")]

            embedding = SimpleNamespace(aembed_documents=AsyncMock(side_effect=lambda texts: [[1.0] + [0.0] * 1023 for _ in texts]),
                http_client=SimpleNamespace(close=lambda: None), http_async_client=SimpleNamespace(aclose=AsyncMock()))
            monkeypatch.setattr("melonclaw.services.knowledge_processing.recognize_document", recognize)
            monkeypatch.setattr("melonclaw.services.knowledge_processing.build_knowledge_embeddings", lambda _: embedding)
            await KnowledgeProcessor(settings, repo).run(job)
            search = KnowledgeSearchService(repo, settings)
            result = await search.search("owner", base["id"], {"query": "收益", "mode": "keyword", "top_k": 6})
            assert result["items"]
            for item in result["items"]:
                assert item["revision_id"] == uploaded["revision_id"]
                assert item["source_locator"] == {"page": 1, "method": "ocr"}
                text = await search.open_text("owner", base["id"], item["document_id"],
                    revision_id=item["revision_id"], offset=item["start_offset"], limit=len(item["content"]))
                assert text["content"] == item["content"] and text["is_current"]
            path, metadata = await KnowledgeDocuments(settings, repo).download("owner", base["id"], uploaded["document_id"])
            assert path.read_bytes() == original and metadata["display_name"].endswith(".docx")
    asyncio.run(run())
