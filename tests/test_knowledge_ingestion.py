"""导入链路的固定协议、真实解析进程、偏移和取消占用验证。"""
import asyncio
import hashlib
import io
import json
import stat
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from melonclaw.core.config import Settings
from melonclaw.core.knowledge import (
    KnowledgeConflict,
    KnowledgeDenied,
    KnowledgeError,
    require_role,
)
from melonclaw.core.knowledge_ocr import (
    OCR_URL,
    OcrPageError,
    OcrPageResult,
    recognize_document,
    strip_layout,
)
from melonclaw.repository.knowledge_documents import (
    KnowledgeDocumentsRepositoryMixin,
    upload_digest,
)
from melonclaw.services.knowledge_documents import SUPPORTED, KnowledgeDocuments
from melonclaw.services.knowledge_executor import KnowledgeExecutor
from melonclaw.services.knowledge_processing import (
    KnowledgeProcessingError,
    KnowledgeProcessor,
    assemble_pages,
    parse_in_process,
    processing_spec,
    split_content,
    validate_vectors,
)
from melonclaw.storage.knowledge import KnowledgeFiles


def settings(tmp_path):
    return Settings(workspace_root=tmp_path / "workspace", data_root=tmp_path,
                    deepseek_api_key="ocr-test-only",
                    knowledge_embedding_api_key="test-only", knowledge_embedding_url="https://embedding.example/v1",
                    knowledge_embedding_model_name="BAAI/bge-m3", knowledge_embedding_dimensions=1024)


@pytest.mark.parametrize("role,allowed", [("owner", True), ("editor", True), ("viewer", False)])
def test_document_permission_matrix(role, allowed):
    if allowed:
        require_role(role, "documents")
    else:
        with pytest.raises(KnowledgeDenied):
            require_role(role, "documents")


def test_layout_offsets_repeated_chinese_and_emoji():
    raw = "<\uff5cref\uff5c>text<\uff5c/ref\uff5c><\uff5cbox\uff5c>[[0, 0, 5, 5]]<\uff5c/box\uff5c>中文👨‍👩‍👦e\u0301。" * 100
    content, locators = assemble_pages([{"page": 1, "content": strip_layout(raw), "method": "ocr"},
                                       {"page": 2, "content": "第二页" * 100}])
    records = split_content(content, locators, {"chunk_size": 100, "chunk_overlap": 20})
    assert len(records) > 3
    assert {r["source_locator"]["page"] for r in records} == {1, 2}
    assert "<\uff5cbox\uff5c>" not in content
    for record in records:
        assert content[record["start_offset"]:record["end_offset"]] == record["content"]
    with pytest.raises(ValueError):
        assemble_pages([{"page": 1, "content": ""}])
    bounded = split_content("a" * 5000, [{"page": 1, "start": 0, "end": 5000, "method": "text"}],
                            {"chunk_size": 2000, "chunk_overlap": 400})
    assert bounded[1]["start_offset"] - bounded[0]["start_offset"] == 1600
    with pytest.raises(ValueError, match="分块配置超出允许范围"):
        split_content("a" * 1000, [{"page": 1, "start": 0, "end": 1000, "method": "text"}],
                      {"chunk_size": 8000, "chunk_overlap": 2000})


@pytest.mark.parametrize("vectors,count,dimensions", [([], 1, 2), ([[1]], 1, 2), ([[0, 0]], 1, 2),
                                                      ([[float("nan"), 1]], 1, 2), ([[float("inf"), 1]], 1, 2)])
def test_reject_invalid_vectors(vectors, count, dimensions):
    with pytest.raises(ValueError):
        validate_vectors(vectors, count, dimensions)


def test_vectors_are_serialized_as_bound_values():
    assert json.loads(validate_vectors([[1, 0]], 1, 2)[0]) == [1, 0]


def ocr_payload(pages, purpose="text_recognition"):
    return {"object": "ocr.result", "purpose": purpose, "page_count": len(pages), "pages": pages}


def test_ocr_request_order_page_mapping_and_layout_stripping(tmp_path, monkeypatch):
    requests = []
    marked = "<\uff5cref\uff5c>标题<\uff5c/ref\uff5c><\uff5cbox\uff5c>[[52, 40, 948, 96]]<\uff5c/box\uff5c>第一页"
    pages = [{"index": 0, "text": marked, "finish_reason": "stop"},
             {"index": 1, "text": "第二页", "finish_reason": "stop"}]
    ocr_transport(monkeypatch, lambda request: httpx.Response(200, content=b"\n\n" + json.dumps(ocr_payload(pages)).encode()), requests)
    result = asyncio.run(recognize_document(settings(tmp_path), b"%PDF-1.4", ".pdf"))
    body = requests[0].content
    assert body.find(b'name="purpose"') < body.find(b'name="file"')
    assert b"text_recognition" in body and b'filename="document.pdf"' in body
    assert b"max_tokens" not in body and b'"model"' not in body
    assert str(requests[0].url) == OCR_URL and requests[0].headers["Authorization"] == "Bearer ocr-test-only"
    assert len(requests) == 1 and "ocr-test-only" not in repr(settings(tmp_path))
    assert [(item.page, item.text, item.finish_reason) for item in result] == [(1, "第一页", "stop"), (2, "第二页", "stop")]
    assert result[0].raw == marked and "<\uff5cbox\uff5c>" not in result[0].text


@pytest.mark.parametrize("extension,purpose,filename", [
    (".png", "rich_layout_recognition", "document.png"),
    (".jpeg", "rich_layout_recognition", "document.jpeg"),
    (".webp", "rich_layout_recognition", "document.webp"),
    (".gif", "rich_layout_recognition", "document.gif"),
    (".bmp", "rich_layout_recognition", "document.bmp"),
    (".tif", "rich_layout_recognition", "document.tif"),
    (".tiff", "rich_layout_recognition", "document.tiff"),
    (".pdf", "text_recognition", "document.pdf"),
    (".docx", "text_recognition", "document.docx"),
    (".pptx", "text_recognition", "document.pptx"),
])
def test_image_ocr_uses_rich_layout_and_documents_use_text(tmp_path, monkeypatch, extension, purpose, filename):
    requests = []
    ocr_transport(monkeypatch, lambda request: httpx.Response(
        200, json=ocr_payload([{"index": 0, "text": "正文", "finish_reason": "stop"}], purpose)), requests)
    result = asyncio.run(recognize_document(settings(tmp_path), b"file-bytes", extension))
    body = requests[0].content
    assert result[0].text == "正文"
    assert purpose.encode() in body and filename.encode() in body
    other = b"text_recognition" if purpose == "rich_layout_recognition" else b"rich_layout_recognition"
    assert other not in body


def test_ocr_missing_key_or_timeout_does_not_call_network(tmp_path, monkeypatch):
    requests = []

    def reject(request):
        raise AssertionError(request.url)

    ocr_transport(monkeypatch, reject, requests)
    configs = [
        replace(settings(tmp_path), deepseek_api_key=""),
        replace(settings(tmp_path), deepseek_api_key="   "),
        replace(settings(tmp_path), knowledge_ocr_timeout_seconds=0),
    ]
    for config in configs:
        with pytest.raises(OcrPageError) as caught:
            asyncio.run(recognize_document(config, b"%PDF-1.4", ".pdf"))
        assert caught.value.category == "ocr_configuration"
        assert "ocr-test-only" not in str(caught.value) and OCR_URL not in str(caught.value)
    assert requests == []


def test_real_isolated_process_reads_text_and_rejects_other_types(tmp_path):
    async def run():
        config = settings(tmp_path)
        text = tmp_path / "input.txt"
        text.write_text("中文\n测试👨‍👩‍👦")
        pages = await parse_in_process(config, text, ".txt", tmp_path / "text-work")
        assert pages == [{"page": 1, "content": text.read_text()}]
        markdown = tmp_path / "input.md"
        markdown.write_text("# 标题\n正文")
        pages = await parse_in_process(config, markdown, ".md", tmp_path / "md-work")
        assert pages[0]["content"] == "# 标题\n正文"
        pdf = tmp_path / "input.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with pytest.raises(KnowledgeProcessingError) as caught:
            await parse_in_process(config, pdf, ".pdf", tmp_path / "pdf-work")
        assert caught.value.code == "parse_unsupported"
        assert str(tmp_path) not in caught.value.summary
    asyncio.run(run())


def test_knowledge_processor_publishes_txt_and_cleans_up(tmp_path, monkeypatch):
    class FakeEmbeddings:
        def __init__(self):
            self.embed_calls = []
            self.sync_client_closed = False
            self.async_client_closed = False
            self.http_client = SimpleNamespace(close=self.close_sync_client)
            self.http_async_client = SimpleNamespace(aclose=self.close_async_client)

        async def aembed_documents(self, texts):
            self.embed_calls.append(list(texts))
            return [[0.01] * 1024 for _ in texts]

        def close_sync_client(self):
            self.sync_client_closed = True

        async def close_async_client(self):
            self.async_client_closed = True

    class RecordingRepository:
        def __init__(self):
            self.stages = []
            self.published = []

        async def fixed_embedding_model_id(self, config):
            return "fixed-development-model"

        async def knowledge_job_check(self, job, stage=None):
            if stage:
                self.stages.append(stage)

        async def knowledge_publish(self, job, content, records):
            self.published.append((job, content, records))

    async def run():
        config = settings(tmp_path)
        files = KnowledgeFiles(config.data_root)
        kb_id, document_id, revision_id, job_id = (uuid4() for _ in range(4))
        source = files.source(kb_id, document_id, revision_id)
        source.parent.mkdir(parents=True, exist_ok=True)
        source_content = "真实 TXT 解析：中文 / English / 🌱 / e\u0301。\n保留 UTF-8 内容。"
        source_bytes = source_content.encode("utf-8")
        source.write_bytes(source_bytes)

        spec = processing_spec(".txt")
        spec["chunk_config"] = {"chunk_size": 800, "chunk_overlap": 120}
        job = {
            "id": job_id,
            "kb_id": kb_id,
            "document_id": document_id,
            "revision_id": revision_id,
            "knowledge_mode": "vector",
            "revision": {
                "processing_spec": spec,
                "source_byte_size": len(source_bytes),
                "source_hash": hashlib.sha256(source_bytes).hexdigest(),
            },
        }
        repo = RecordingRepository()
        embeddings = FakeEmbeddings()
        monkeypatch.setattr(
            "melonclaw.services.knowledge_processing.build_knowledge_embeddings",
            lambda _settings: embeddings,
        )
        monkeypatch.setattr(
            "melonclaw.services.knowledge_processing.recognize_document",
            AsyncMock(side_effect=AssertionError("TXT 不得外发")),
        )
        threaded = []
        real_to_thread = asyncio.to_thread

        async def spy_to_thread(func, /, *args, **kwargs):
            threaded.append(getattr(func, "__name__", type(func).__name__))
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr("melonclaw.services.knowledge_processing.asyncio.to_thread", spy_to_thread)

        work = files.work(job_id)
        raw_path = files.path(kb_id, document_id, revision_id, "ocr.json")
        await KnowledgeProcessor(config, repo).run(job)

        assert repo.stages == ["chunking", "embedding", "publishing"]
        assert len(embeddings.embed_calls) == 1
        assert embeddings.embed_calls[0] == [source_content]
        assert len(repo.published) == 1
        published_job, published_content, records = repo.published[0]
        assert published_job is job
        assert published_content == source_content
        assert len(records) == 1
        assert records[0]["content"] == source_content
        assert records[0]["start_offset"] == 0
        assert records[0]["end_offset"] == len(source_content)
        assert records[0]["source_locator"] == {"page": 1, "method": "text"}
        vector = json.loads(records[0]["vector"])
        assert len(vector) == config.knowledge_embedding_dimensions
        assert all(value == 0.01 for value in vector)
        assert raw_path.read_text(encoding="utf-8") == "[]"
        assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
        assert embeddings.sync_client_closed
        assert embeddings.async_client_closed
        assert not work.exists()
        assert threaded == ["source_sha256", "split_content"]

    asyncio.run(run())


def test_storage_rejects_symlinks_and_arbitrary_paths(tmp_path):
    files = KnowledgeFiles(tmp_path)
    with pytest.raises(ValueError):
        files.path("../escape")
    files.root.symlink_to(tmp_path / "other")
    with pytest.raises(ValueError):
        files.path(uuid4())


def test_upload_limit_and_idempotent_file_cleanup(tmp_path):
    from starlette.datastructures import UploadFile
    async def run():
        config = replace(settings(tmp_path), knowledge_max_file_bytes=4)
        repo = SimpleNamespace(knowledge_detail=AsyncMock(return_value={"role": "editor", "knowledge_mode": "vector", "active_pipeline_job_id": None}), knowledge_upload=AsyncMock())
        service = KnowledgeDocuments(config, repo)
        with pytest.raises(ValueError):
            await service.upload("user", uuid4(), UploadFile(io.BytesIO(b"large"), filename="demo.txt"), "request")
        repo.knowledge_upload.assert_not_called()
        assert not list(service.files.root.rglob("source"))
        repo.knowledge_upload.return_value = {"revision_id": uuid4()}
        await service.upload("user", uuid4(), UploadFile(io.BytesIO(b"ok"), filename="demo.txt"), "request")
        assert not list(service.files.root.rglob("source"))
    asyncio.run(run())


def test_upload_accepts_deepseek_types_and_rejects_xlsx(tmp_path):
    from starlette.datastructures import UploadFile

    async def run():
        config = settings(tmp_path)
        repo = SimpleNamespace(knowledge_detail=AsyncMock(return_value={"role": "editor", "knowledge_mode": "vector", "active_pipeline_job_id": None}), knowledge_upload=AsyncMock())

        async def uploaded(*_args, **kwargs):
            return {"revision_id": kwargs["revision_id"]}

        repo.knowledge_upload.side_effect = uploaded
        service = KnowledgeDocuments(config, repo)
        accepted = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".txt", ".md"}
        assert set(SUPPORTED) == accepted
        for extension in sorted(accepted):
            await service.upload("user", uuid4(), UploadFile(io.BytesIO(b"x"), filename="资料" + extension), str(uuid4()))
            submitted = repo.knowledge_upload.call_args.kwargs
            assert submitted["mime_type"] == SUPPORTED[extension]
            assert submitted["processing_spec"] == {
                "extension": extension, "parser_version": 3, "source_name": "资料" + extension,
            }
        with pytest.raises(KnowledgeError, match="Markdown"):
            await service.upload("user", uuid4(), UploadFile(io.BytesIO(b"sheet"), filename="研究.xlsx"), "request")
    asyncio.run(run())


def test_executor_blocks_only_the_busy_library(tmp_path):
    executor = KnowledgeExecutor(settings(tmp_path), SimpleNamespace())
    first = {"operation": "upload", "document_id": "d1", "kb_id": "kb1", "requested_by": "alice"}
    other_library = {"operation": "upload", "document_id": "d2", "kb_id": "kb2", "requested_by": "alice"}
    same_library = {"operation": "upload", "document_id": "d3", "kb_id": "kb1", "requested_by": "bob"}
    assert executor.runnable(other_library, [first])
    assert not executor.runnable(same_library, [first])
    assert not executor.runnable(first, [first])
    active = [{"operation": "upload", "document_id": f"d{i}", "kb_id": f"kb{i}", "requested_by": "alice"} for i in range(executor.settings.knowledge_concurrency)]
    assert not executor.runnable({"operation": "upload", "document_id": "dx", "kb_id": "kbx", "requested_by": "alice"}, active)


def test_cancelled_job_keeps_local_occupancy_until_process_exits(tmp_path):
    async def run():
        entered, stopping, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def process(job):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopping.set()
                await exited.wait()
        repo = SimpleNamespace(knowledge_job_check=AsyncMock(side_effect=KnowledgeConflict("cancelled")),
                               knowledge_finish_failure=AsyncMock(), knowledge_release_pipeline=AsyncMock())
        executor = KnowledgeExecutor(settings(tmp_path), repo)
        executor.processor.run = process
        job = {"id": uuid4(), "document_id": uuid4(), "requested_by": "user", "operation": "upload"}
        executor.active[job["id"]] = job
        task = asyncio.create_task(executor.execute(job))
        await entered.wait()
        await asyncio.wait_for(stopping.wait(), 2)
        assert job["id"] in executor.active and not task.done()
        repo.knowledge_release_pipeline.assert_not_awaited()
        exited.set()
        await task
        assert job["id"] not in executor.active
        repo.knowledge_release_pipeline.assert_awaited_once_with(job)
    asyncio.run(run())


def test_search_index_contains_internal_chinese_terms():
    from melonclaw.services.knowledge_processing import split_content

    text = "中华人民共和国南京市长江大桥"
    records = split_content(text, [{"page": 1, "start": 0, "end": len(text), "method": "text"}],
                            {"chunk_size": 800, "chunk_overlap": 120})
    assert records[0]["content"] == text
    assert {"人民", "共和国", "南京", "长江", "大桥"} <= set(records[0]["search_text"].split())


def ocr_transport(monkeypatch, handler, requests):
    import melonclaw.core.knowledge_ocr as module
    original = httpx.AsyncClient

    def record(request):
        requests.append(request)
        return handler(request)

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(record))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)


def test_ocr_total_timeout_closes_response_despite_keepalives(tmp_path, monkeypatch):
    class KeepaliveStream(httpx.AsyncByteStream):
        keepalives = 0
        closed = False

        async def __aiter__(self):
            for _ in range(20):
                self.keepalives += 1
                yield b"\n"
                await asyncio.sleep(0.01)
            yield json.dumps(ocr_payload([
                {"index": 0, "text": "正文", "finish_reason": "stop"},
            ])).encode()

        async def aclose(self):
            self.closed = True

    stream = KeepaliveStream()
    requests = []
    ocr_transport(monkeypatch, lambda _: httpx.Response(200, stream=stream), requests)

    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(
            replace(settings(tmp_path), knowledge_ocr_timeout_seconds=0.05), b"%PDF-1.4", ".pdf"))

    assert caught.value.category == "ocr_timeout"
    assert stream.keepalives > 0 and stream.closed
    assert len(requests) == 1


def test_ocr_http_200_error_and_blank_page_do_not_leak_vendor_text(tmp_path, monkeypatch):
    message = "The file is password-protected and cannot be processed. Remove the password and upload it again."
    marked = "<\uff5cref\uff5c>image<\uff5c/ref\uff5c><\uff5cbox\uff5c>[[1, 2, 3, 4]]<\uff5c/box\uff5c>"
    responses = [
        httpx.Response(200, content=b"\n" + json.dumps({"error": {"message": message, "code": "invalid_request_error"}}).encode()),
        httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "第一页", "finish_reason": "stop"},
            {"index": 1, "text": marked, "finish_reason": "stop"},
        ])),
    ]
    ocr_transport(monkeypatch, lambda _request: responses.pop(0), [])
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), b"%PDF-1.4", ".pdf"))
    assert caught.value.category == "ocr_password" and caught.value.page is None
    assert message not in str(caught.value) and OCR_URL not in str(caught.value)
    assert "ocr-test-only" not in str(caught.value)
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), b"%PDF-1.4", ".pdf"))
    assert caught.value.category == "ocr_empty_text" and caught.value.page == 2
    assert marked not in str(caught.value)


def pdf_pages(texts, image_page=None):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for index, text in enumerate(texts):
        writer.add_blank_page(width=612, height=792)
        page = writer.pages[-1]
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        if image_page == index:
            image = DecodedStreamObject()
            image.set_data(bytes((255, 255, 255)))
            image.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(1),
                NameObject("/Height"): NumberObject(1),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            })
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): image})
            content = b"q /Im0 Do Q"
        else:
            content = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = stream
        page[NameObject("/Resources")] = resources
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_blank_pdf_page_gets_its_own_error_when_ocr_fails(tmp_path, monkeypatch):
    document = pdf_pages(["PAGE ONE", "", "PAGE THREE"])
    pictured = pdf_pages([""], image_page=0)
    responses = [
        httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "PAGE ONE", "finish_reason": "stop"},
            {"index": 1, "text": "<table><tr><td></td></tr></table>", "finish_reason": "length"},
            {"index": 2, "text": "", "finish_reason": "stop"},
        ])),
        httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "PAGE ONE", "finish_reason": "stop"},
            {"index": 1, "text": "", "finish_reason": "stop"},
            {"index": 2, "text": "PAGE THREE", "finish_reason": "stop"},
        ])),
        httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "被截断", "finish_reason": "length"},
            {"index": 1, "text": "", "finish_reason": "stop"},
            {"index": 2, "text": "PAGE THREE", "finish_reason": "stop"},
        ])),
        httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "2.2.2.2", "finish_reason": "length"},
        ])),
    ]
    ocr_transport(monkeypatch, lambda _request: responses.pop(0), [])
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), document, ".pdf"))
    assert caught.value.category == "ocr_blank_page" and caught.value.page == 2
    assert "空白页" in caught.value.safe_detail and "<table>" not in str(caught.value)
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), document, ".pdf"))
    assert caught.value.category == "ocr_blank_page" and caught.value.page == 2
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), document, ".pdf"))
    assert caught.value.category == "ocr_truncated" and caught.value.page == 1
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), pictured, ".pdf"))
    assert caught.value.category == "ocr_truncated" and caught.value.page == 1


def test_ocr_rejects_length_gap_and_local_page_limit(tmp_path, monkeypatch):
    responses = [
        httpx.Response(200, json=ocr_payload([{"index": 0, "text": "被截断", "finish_reason": "length"}], "rich_layout_recognition")),
        httpx.Response(200, json=ocr_payload([{"index": 1, "text": "跳页", "finish_reason": "stop"}], "rich_layout_recognition")),
        httpx.Response(200, json=ocr_payload(
            [{"index": index, "text": "页", "finish_reason": "stop"} for index in range(3)], "rich_layout_recognition")),
    ]
    ocr_transport(monkeypatch, lambda _request: responses.pop(0), [])
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), b"png", ".png"))
    assert caught.value.category == "ocr_truncated" and caught.value.page == 1
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), b"png", ".png"))
    assert caught.value.category == "ocr_invalid_response"
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(replace(settings(tmp_path), knowledge_max_pdf_pages=2), b"png", ".png"))
    assert caught.value.category == "ocr_page_limit" and "1000" not in str(caught.value)


@pytest.mark.parametrize("status,body,expected", [
    (429, "provider detail", "ocr_rate_limited"), (503, "busy", "ocr_overloaded"),
    (401, "denied", "ocr_authentication"), (402, "insufficient", "ocr_balance"),
    (408, "slow", "ocr_upload_slow"), (413, "large", "ocr_file_too_large"),
    (504, "late", "ocr_timeout"), (404, "missing", "ocr_http_error"),
    (400, json.dumps({"error": {"message": "Failed to recognize the file: the image could not be decoded"}}), "ocr_unsupported"),
    (200, "\n" + json.dumps({"error": {
        "code": "gateway_timeout_error", "message": "OCR did not finish in time",
    }}), "ocr_recognition_timeout"),
])
def test_ocr_http_failures_have_safe_categories(tmp_path, monkeypatch, status, body, expected):
    requests = []
    ocr_transport(monkeypatch, lambda request: httpx.Response(status, text=body), requests)
    with pytest.raises(OcrPageError) as caught:
        asyncio.run(recognize_document(settings(tmp_path), b"png", ".png"))
    assert caught.value.category == expected
    assert body not in str(caught.value) and "provider" not in str(caught.value)


class _FakeEmbeddings:
    def __init__(self):
        self.http_client = SimpleNamespace(close=lambda: None)
        self.http_async_client = SimpleNamespace(aclose=AsyncMock())

    async def aembed_documents(self, texts):
        return [[0.01] * 1024 for _ in texts]


def processor_job(tmp_path, config):
    files = KnowledgeFiles(config.data_root)
    kb_id, document_id, revision_id, job_id = (uuid4() for _ in range(4))
    source = files.source(kb_id, document_id, revision_id)
    source.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = b"%PDF-1.4 workflow fixture"
    source.write_bytes(source_bytes)
    spec = processing_spec(".pdf")
    spec["chunk_config"] = {"chunk_size": 800, "chunk_overlap": 120}
    job = {"id": job_id, "kb_id": kb_id, "document_id": document_id, "revision_id": revision_id,
           "knowledge_mode": "vector", "revision": {"processing_spec": spec, "source_byte_size": len(source_bytes),
                        "source_hash": hashlib.sha256(source_bytes).hexdigest()}}
    return files, job, source


def recording_repository():
    class Repository:
        def __init__(self):
            self.stages = []
            self.published = []

        async def fixed_embedding_model_id(self, config):
            return "fixed-development-model"

        async def knowledge_job_check(self, job, stage=None):
            if stage:
                self.stages.append(stage)

        async def knowledge_publish(self, job, content, records):
            self.published.append((content, records))

    return Repository()


def test_processor_retry_uses_platform_key_and_ignores_saved_ocr_target(tmp_path, monkeypatch):
    async def run():
        config = settings(tmp_path)
        _, job, _ = processor_job(tmp_path, config)
        job["revision"]["processing_spec"].update(ocr_url="https://ocr.example/v1/tools/ocr",
                                                  ocr_purpose="text_recognition")
        responses = [httpx.Response(401), httpx.Response(200, json=ocr_payload([
            {"index": 0, "text": "识别正文", "finish_reason": "stop"},
        ]))]
        requests = []
        ocr_transport(monkeypatch, lambda _: responses.pop(0), requests)
        monkeypatch.setattr("melonclaw.services.knowledge_processing.build_knowledge_embeddings",
                            lambda _: _FakeEmbeddings())
        repo = recording_repository()
        processor = KnowledgeProcessor(config, repo)

        with pytest.raises(KnowledgeProcessingError) as caught:
            await processor.run(job)
        assert caught.value.code == "ocr_authentication" and not repo.published

        await processor.run({**job, "id": uuid4()})

        assert [str(request.url) for request in requests] == [OCR_URL, OCR_URL]
        assert [request.headers["Authorization"] for request in requests] == [
            "Bearer ocr-test-only", "Bearer ocr-test-only",
        ]
        assert "ocr.example" not in str(requests[0].url) and "ocr.example" not in str(requests[1].url)
        assert len(repo.published) == 1 and repo.published[0][0] == "识别正文"

    asyncio.run(run())


@pytest.mark.parametrize("historical_embedding_spec", [False, True])
def test_processor_publishes_whole_document_ocr_pages(tmp_path, monkeypatch, historical_embedding_spec):
    async def run():
        config = settings(tmp_path)
        files, job, source = processor_job(tmp_path, config)
        if historical_embedding_spec:
            job["revision"]["processing_spec"].update(
                embedding_model="old-model", embedding_url="https://old.example/v1", embedding_dimensions=512,
            )
        work = files.work(job["id"])

        async def fake_parse(*_args, **_kwargs):
            raise AssertionError("整份 OCR 不得再进入本地解析进程")

        async def fake_ocr(_config, content, extension):
            assert extension == ".pdf" and content == source.read_bytes()
            return [OcrPageResult(page=1, raw="raw-1", text="第一页真实OCR正文", finish_reason="stop"),
                    OcrPageResult(page=2, raw="raw-2", text="第二页真实OCR正文", finish_reason="stop")]

        def fake_embeddings(current_settings):
            assert current_settings is config
            return _FakeEmbeddings()

        monkeypatch.setattr("melonclaw.services.knowledge_processing.parse_in_process", fake_parse)
        monkeypatch.setattr("melonclaw.services.knowledge_processing.recognize_document", fake_ocr)
        monkeypatch.setattr("melonclaw.services.knowledge_processing.build_knowledge_embeddings",
                            fake_embeddings)
        repo = recording_repository()
        await KnowledgeProcessor(config, repo).run(job)

        assert repo.stages == ["chunking", "embedding", "publishing"]
        assert len(repo.published) == 1
        content, records = repo.published[0]
        assert {r["source_locator"]["page"] for r in records} == {1, 2}
        assert all(set(r["source_locator"]) == {"page", "method"} and r["source_locator"]["method"] == "ocr" for r in records)
        assert all(content[r["start_offset"]:r["end_offset"]] == r["content"] for r in records)
        raw_path = files.path(job["kb_id"], job["document_id"], job["revision_id"], "ocr.json")
        assert json.loads(raw_path.read_text()) == [{"page": 1, "raw": "raw-1", "finish_reason": "stop"},
                                                    {"page": 2, "raw": "raw-2", "finish_reason": "stop"}]
        assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
        assert not work.exists()
    asyncio.run(run())


def test_processor_never_publishes_when_an_ocr_page_has_no_text(tmp_path, monkeypatch):
    async def run():
        config = settings(tmp_path)
        files, job, _ = processor_job(tmp_path, config)
        work = files.work(job["id"])

        async def fake_ocr(_config, _content, _extension):
            raise OcrPageError("ocr_empty_text", 2)

        monkeypatch.setattr("melonclaw.services.knowledge_processing.parse_in_process",
                            AsyncMock(side_effect=AssertionError("失败的 OCR 不得改走本地解析")))
        monkeypatch.setattr("melonclaw.services.knowledge_processing.recognize_document", fake_ocr)
        repo = recording_repository()
        with pytest.raises(KnowledgeProcessingError) as caught:
            await KnowledgeProcessor(config, repo).run(job)
        error = caught.value
        assert error.code == "ocr_empty_text" and error.page == 2
        assert "第 2 页" in error.summary
        assert "ocr.example" not in error.summary and str(tmp_path) not in error.summary
        assert repo.published == [] and not work.exists()
    asyncio.run(run())


def test_executor_persists_page_scoped_failure(tmp_path):
    async def run():
        repo = SimpleNamespace(knowledge_job_check=AsyncMock(), knowledge_finish_failure=AsyncMock(), knowledge_release_pipeline=AsyncMock())
        executor = KnowledgeExecutor(settings(tmp_path), repo)
        failure = KnowledgeProcessingError("ocr_empty_text", "两次识别都没有返回可用正文。", 13)

        async def fail(_job):
            raise failure

        executor.processor.run = fail
        job = {"id": uuid4(), "document_id": uuid4(), "requested_by": "user", "operation": "upload"}
        await executor.execute(job)
        repo.knowledge_finish_failure.assert_awaited_once()
        args = repo.knowledge_finish_failure.await_args.args
        assert args[1] == "ocr_empty_text" and "第 13 页" in args[2]
        assert executor.active == {}
    asyncio.run(run())


@pytest.mark.parametrize(("extension", "stage", "repeats_ocr"), [
    (".pdf", "chunking", True),
    (".pptx", "embedding", True),
    (".png", "publishing", True),
    (".pdf", "parsing", False),
    (".txt", "embedding", False),
])
def test_job_failure_warns_about_repeat_ocr_after_recognition(extension, stage, repeats_ocr):
    conn = SimpleNamespace(execute=AsyncMock())
    job = {"id": uuid4(), "run_token": uuid4()}

    @asynccontextmanager
    async def transaction(_job):
        row = {**job, "status": "running", "stage": stage}
        yield conn, None, None, {"processing_spec": {"extension": extension}}, row

    repo = KnowledgeDocumentsRepositoryMixin()
    repo.knowledge_job_transaction = transaction
    asyncio.run(repo.knowledge_finish_failure(job, "processing_failed", "导入失败。"))

    summary = conn.execute.await_args.args[0].compile().params["error_summary"]
    assert summary.startswith("导入失败。")
    assert ("可能再次计费" in summary) is repeats_ocr


def test_upload_digest_changes_when_chunk_config_changes():
    base = {"extension": ".txt", "chunk_config": {"chunk_size": 800, "chunk_overlap": 120}}
    changed = {**base, "chunk_config": {"chunk_size": 2000, "chunk_overlap": 120}}
    first = upload_digest("kb", None, "a.txt", "ab" * 32, base, replace=False)
    assert first != upload_digest("kb", None, "a.txt", "ab" * 32, changed, replace=False)
    assert first == upload_digest("kb", None, "a.txt", "ab" * 32, dict(base), replace=False)


def test_claim_conflict_finishes_only_that_job(tmp_path):
    async def run():
        repo = SimpleNamespace(
            knowledge_claim=AsyncMock(side_effect=KnowledgeConflict("知识库流程占用不一致。")),
            knowledge_finish_failure=AsyncMock(),
        )
        executor = KnowledgeExecutor(settings(tmp_path), repo)
        executor.ready = True
        assert await executor.claim_queued({"id": "job-1", "operation": "upload"}) is None
        assert executor.ready is True
        repo.knowledge_finish_failure.assert_awaited_once()
        assert repo.knowledge_finish_failure.await_args.args[0]["id"] == "job-1"
    asyncio.run(run())
