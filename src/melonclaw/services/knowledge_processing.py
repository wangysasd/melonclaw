"""受控子进程解析、整份 OCR、原样分块与向量验证。"""
import asyncio
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langsmith import tracing_context

from melonclaw.core.knowledge_embedding import build_knowledge_embeddings
from melonclaw.core.knowledge_ocr import OcrPageError, recognize_document
from melonclaw.storage.knowledge import KnowledgeFiles

PARSE_ERROR_CODES = frozenset({"parse_failed", "text_limit", "unsupported", "empty"})
TEXT_EXTENSIONS = {".txt", ".md"}
PARSER_VERSION = 3


class KnowledgeProcessingError(ValueError):
    """带固定分类与页码的导入失败；摘要不含凭据、URL、正文或主机路径。"""

    def __init__(self, code, detail, page=None):
        self.code = code
        self.page = page
        location = f"第 {page} 页：" if isinstance(page, int) and page > 0 else ""
        self.summary = f"{location}{detail}"[:1000]
        super().__init__(self.summary)


def processing_spec(extension):
    return {"extension": extension, "parser_version": PARSER_VERSION}


async def parse_in_process(settings, source, extension, output):
    worker = Path(__file__).resolve().parents[1] / "parsers" / "knowledge_worker.py"
    spec = {"source": str(source), "extension": extension, "output": str(output),
            "max_chars": settings.knowledge_max_chars, "cpu_seconds": settings.knowledge_parse_timeout_seconds,
            "max_file_bytes": settings.knowledge_max_file_bytes}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(worker), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.defpath, "LANG": "en_US.UTF-8"},
    )
    try:
        async with asyncio.timeout(settings.knowledge_parse_timeout_seconds):
            output_bytes, _ = await process.communicate(json.dumps(spec).encode())
        if process.returncode or len(output_bytes) > settings.knowledge_max_chars * 6 + 100000:
            raise _parse_failure(output_bytes, settings.knowledge_max_pdf_pages)
        return json.loads(output_bytes)["pages"]
    finally:
        if process.returncode is None:
            process.kill()
            # 取消后必须等进程实际退出，再释放执行槽。
            await process.wait()


def _parse_failure(output_bytes, max_pages):
    """把子进程的固定分类与页码转成安全摘要，不回显 stdout 中的正文。"""

    code, page = "parse_failed", None
    try:
        error = json.loads(output_bytes).get("error")
        if isinstance(error, dict):
            candidate = str(error.get("code") or "")
            if candidate in PARSE_ERROR_CODES:
                code = candidate
            value = error.get("page")
            if type(value) is int and 0 < value <= max_pages:
                page = value
    except (ValueError, AttributeError, TypeError):
        pass
    detail = {
        "text_limit": "正文超过容量限制。请拆分文件后上传。",
        "unsupported": "文件格式不受支持。请换成支持的格式后上传。",
        "empty": "没有可用正文。请检查文件内容。",
    }.get(code, "无法读取文本。请确认文件以 UTF-8 保存后重试或替换。")
    return KnowledgeProcessingError(f"parse_{code}", detail, page)


def assemble_pages(pages):
    content, locators = "", []
    for page in pages:
        text = page["content"].strip()
        if not text:
            raise KnowledgeProcessingError("empty_page_text", "没有可用正文。请检查文件内容。", page["page"])
        if content:
            content += "\n\n"
        start = len(content)
        content += text
        locators.append({"page": page["page"], "start": start, "end": len(content),
                         "method": page.get("method", "text"), **page.get("source_locator", {})})
    return content, locators


def split_content(content, locators, config):
    # 块大小与保存的配置一致；重叠最多为块大小的 20%，避免近重复切片。
    size, overlap = config["chunk_size"], config["chunk_overlap"]
    if type(size) is not int or type(overlap) is not int or not 100 <= size <= 2000 or overlap < 0 or overlap > size // 5:
        raise ValueError("分块配置超出允许范围。")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size, chunk_overlap=overlap, add_start_index=True,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
        strip_whitespace=False,
    )
    result = []
    for locator in locators:
        page_text = content[locator["start"]:locator["end"]]
        for document in splitter.create_documents([page_text]):
            start = locator["start"] + document.metadata["start_index"]
            text = document.page_content
            if start < locator["start"] or content[start:start+len(text)] != text:
                raise ValueError("分块来源偏移无效。")
            if len(text.encode()) > 8192:
                raise ValueError("分块超过嵌入输入预算。")
            result.append({"ordinal": len(result), "content": text, "start_offset": start,
                           "end_offset": start+len(text),
                           "source_locator": {key: value for key, value in locator.items()
                                              if key not in {"start", "end"} and value is not None},
                           "search_text": " ".join(jieba.cut_for_search(text, HMM=False))})
    if not result or len(result) > 10000:
        raise ValueError("文档分块数量无效。")
    return result


def source_sha256(path: Path):
    """在线程中流式计算原件摘要，避免把整文件读进事件循环。"""

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def validate_vectors(vectors, count, dimensions):
    if len(vectors) != count:
        raise ValueError("嵌入返回数量不一致。")
    for vector in vectors:
        if len(vector) != dimensions or not all(type(v) in {int, float} and math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("嵌入向量无效。")
    return [json.dumps(v, allow_nan=False) for v in vectors]


class KnowledgeProcessor:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        self.files = KnowledgeFiles(settings.data_root)

    async def run(self, job):
        settings, repo = self.settings, self.repository
        spec = job["revision"]["processing_spec"]
        if spec.get("parser_version") != PARSER_VERSION:
            raise KnowledgeProcessingError("parser_version_changed", "文档解析规则已更新，请重新上传。")
        try:
            await repo.fixed_embedding_model_id(settings)
        except ValueError:
            raise KnowledgeProcessingError(
                "embedding_configuration", "嵌入配置无效或与知识库不匹配。请联系维护人员检查后重试。"
            ) from None
        source = self.files.source(job["kb_id"], job["document_id"], job["revision_id"])
        size, digest = await asyncio.to_thread(source_sha256, source)
        if size != job["revision"]["source_byte_size"] or digest != job["revision"]["source_hash"]:
            raise KnowledgeProcessingError("source_changed", "保存的原件校验失败。请替换文件。")
        work = self.files.work(job["id"])
        work.mkdir(parents=True, exist_ok=False)
        embeddings = None
        try:
            extension = spec["extension"]
            raw_results = []
            if extension in TEXT_EXTENSIONS:
                pages = await parse_in_process(settings, source, extension, work)
                for page in pages:
                    page["method"] = "text"
            else:
                payload = await asyncio.to_thread(source.read_bytes)
                try:
                    with tracing_context(enabled=False):
                        outcomes = await recognize_document(settings, payload, extension)
                except OcrPageError as exc:
                    raise KnowledgeProcessingError(exc.category, exc.safe_detail, exc.page) from None
                await repo.knowledge_job_check(job)
                pages = [{"page": item.page, "content": item.text, "method": "ocr"} for item in outcomes]
                raw_results = [{"page": item.page, "raw": item.raw, "finish_reason": item.finish_reason}
                               for item in outcomes]
            chars = 0
            for page in pages:
                await repo.knowledge_job_check(job)
                chars += len(page["content"])
                if chars > settings.knowledge_max_chars:
                    raise KnowledgeProcessingError("text_limit", "正文超过容量限制。请拆分文件后上传。", page["page"])
            content, locators = assemble_pages(pages)
            if len(content) > settings.knowledge_max_chars:
                raise KnowledgeProcessingError("text_limit", "正文超过容量限制。请拆分文件后上传。")
            await repo.knowledge_job_check(job, "chunking")
            records = await asyncio.to_thread(split_content, content, locators, spec["chunk_config"])
            await repo.knowledge_job_check(job, "embedding")
            embeddings = build_knowledge_embeddings(settings)
            for start in range(0, len(records), 16):
                await repo.knowledge_job_check(job)
                batch = records[start:start+16]
                with tracing_context(enabled=False):
                    vectors = await embeddings.aembed_documents([r["content"] for r in batch])
                encoded = validate_vectors(vectors, len(batch), settings.knowledge_embedding_dimensions)
                for record, vector in zip(batch, encoded, strict=True):
                    record["vector"] = vector
            await repo.knowledge_job_check(job, "publishing")
            # OCR 原始标记为私有派生文件，不进入日志、审计或模型配置。
            raw_path = self.files.path(job["kb_id"], job["document_id"], job["revision_id"], "ocr.json")
            with open(raw_path, "w", encoding="utf-8",
                      opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
                json.dump(raw_results, output, ensure_ascii=False)
            await repo.knowledge_publish(job, content, records)
        finally:
            if embeddings is not None:
                embeddings.http_client.close()
                await embeddings.http_async_client.aclose()
            shutil.rmtree(work, ignore_errors=True)
