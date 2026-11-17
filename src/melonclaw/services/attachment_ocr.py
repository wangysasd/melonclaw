"""聊天附件的 OCR 用途、限额与派生正文。"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from time import monotonic

from langsmith import tracing_context
from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader, PdfWriter

from melonclaw.core.config import Settings
from melonclaw.core.knowledge_ocr import (
    IMAGE_PURPOSE,
    OCR_MAX_FILE_BYTES,
    OCR_MAX_PAGES,
    OcrPageError,
    recognize_file,
)
from melonclaw.parsers.documents import write_document_parts
from melonclaw.parsers.validation import SUPPORTED_TYPES, ValidatedAttachment
from melonclaw.services.attachment_images import first_frame_png

logger = logging.getLogger(__name__)


def requires_document_ocr(name: str, kind: str) -> bool:
    spec = SUPPORTED_TYPES.get(Path(name).suffix.lower())
    return bool(spec and (spec.ocr_required or (spec.kind == "image" and kind == "document")))


def ocr_file_limit(settings: Settings) -> int:
    return min(settings.attachment_max_file_bytes, settings.attachment_max_total_bytes, OCR_MAX_FILE_BYTES)


def ocr_pdf_page_limit(settings: Settings) -> int:
    return min(settings.attachment_pdf_max_pages, OCR_MAX_PAGES)


def _tiff_pdf(source: Path, max_bytes: int) -> bytes:
    """逐页装入 PDF，保留页序并限制累计的压缩内容体积。"""
    writer = PdfWriter()
    page_bytes = 0
    with Image.open(source) as image:
        for frame in ImageSequence.Iterator(image):
            page = BytesIO()
            ImageOps.exif_transpose(frame).convert("RGB").save(page, format="PDF", resolution=150, quality=90)
            page_bytes += page.tell()
            if page_bytes > max_bytes:
                raise OcrPageError("ocr_file_too_large")
            page.seek(0)
            writer.add_page(PdfReader(page).pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def parse_attachment_ocr(
    settings: Settings, source: Path, output: Path, attachment: ValidatedAttachment,
) -> None:
    extension = Path(attachment.name).suffix.lower()
    max_bytes = ocr_file_limit(settings)
    if source.stat().st_size > max_bytes:
        raise OcrPageError("ocr_file_too_large")
    max_pages = ocr_pdf_page_limit(settings) if extension == ".pdf" else OCR_MAX_PAGES
    purpose = None
    if attachment.first_frame_only:
        content = await asyncio.to_thread(first_frame_png, source)
        extension = ".png"
    elif extension in {".tif", ".tiff"} and attachment.kind == "document":
        # DeepSeek 整份 TIFF 实测只返回第一页，PDF 容器可完整逐页识别。
        content = await asyncio.to_thread(_tiff_pdf, source, max_bytes)
        extension = ".pdf"
        purpose = IMAGE_PURPOSE
    else:
        content = await asyncio.to_thread(source.read_bytes)
    if len(content) > max_bytes:
        raise OcrPageError("ocr_file_too_large")

    started = monotonic()
    with tracing_context(enabled=False):
        pages = await recognize_file(
            content, extension, api_key=settings.deepseek_api_key,
            max_pages=max_pages,
            max_chars=settings.attachment_parse_max_chars,
            timeout_seconds=settings.attachment_parse_timeout_seconds,
            purpose=purpose,
        )
    if extension not in {".doc", ".docx", ".ppt", ".pptx"} and len(pages) != attachment.page_count:
        raise OcrPageError("ocr_page_mismatch")

    text = "\n\n".join(f"## 第 {page.page} 页\n\n{page.text}" for page in pages)
    write_document_parts(output, attachment.name, text, settings.attachment_parse_max_chars)
    logger.info("聊天附件 OCR 完成，格式 %s，字节数 %d，页数 %d，耗时 %.2f 秒",
                extension, len(content), len(pages), monotonic() - started)
