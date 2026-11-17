"""开发侧唯一 OCR 调用协议。地址固定为 DeepSeek 官方接口，凭据使用 Settings.deepseek_api_key。

每次提交一个文件。图片使用 rich_layout_recognition，文档使用 text_recognition。
不读取模型目录、个人 Key 或 SDK 默认值。不重试，异常只保留固定分类，不保留供应商错误正文。
"""
from __future__ import annotations

import asyncio
import io
import json
import re
from dataclasses import dataclass

import httpx
from pypdf import PdfReader

OCR_URL = "https://api.deepseek.com/v1/tools/ocr"
OCR_MAX_FILE_BYTES = 100 * 1024 * 1024
OCR_MAX_PAGES = 1_000
OCR_TIMEOUT_SECONDS = 180
DOCUMENT_PURPOSE = "text_recognition"
IMAGE_PURPOSE = "rich_layout_recognition"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_REF = re.compile("<\uff5cref\uff5c>.*?<\uff5c/ref\uff5c>", re.DOTALL)
_BOX = re.compile("<\uff5cbox\uff5c>.*?<\uff5c/box\uff5c>", re.DOTALL)
FILE_PARTS = {
    ".pdf": ("document.pdf", "application/pdf"),
    ".doc": ("document.doc", "application/msword"),
    ".docx": (
        "document.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".ppt": ("document.ppt", "application/vnd.ms-powerpoint"),
    ".pptx": (
        "document.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".png": ("document.png", "image/png"),
    ".jpg": ("document.jpg", "image/jpeg"),
    ".jpeg": ("document.jpeg", "image/jpeg"),
    ".webp": ("document.webp", "image/webp"),
    ".gif": ("document.gif", "image/gif"),
    ".bmp": ("document.bmp", "image/bmp"),
    ".tif": ("document.tif", "image/tiff"),
    ".tiff": ("document.tiff", "image/tiff"),
}

# 安全错误分类：只描述故障类型，不含地址、凭据、正文或供应商响应内容。
SAFE_MESSAGES = {
    "ocr_configuration": "OCR 配置无效。请联系维护人员修正后重试。",
    "ocr_rate_limited": "OCR 请求过多，请稍后重试。",
    "ocr_overloaded": "OCR 服务繁忙，请稍后重试。",
    "ocr_authentication": "OCR 服务鉴权失败。请联系维护人员检查 Key 和权限后重试。",
    "ocr_balance": "OCR 服务余额不足。请在余额恢复后重试。",
    "ocr_upload_slow": "文件上传超时。请在网络稳定后重试，大文件可先压缩或拆分。",
    "ocr_file_too_large": "文件超过 OCR 大小限制。请压缩或拆分后上传。",
    "ocr_http_error": "OCR 请求失败。请稍后重试，若仍失败请联系维护人员检查服务。",
    "ocr_timeout": "等待 OCR 响应超时。请稍后重试，大文件可先拆分。",
    "ocr_recognition_timeout": "OCR 未能在时限内识别完。建议拆小文件后上传。",
    "ocr_transport_error": "连接 OCR 服务失败。请在网络恢复后重试。",
    "ocr_response_too_large": "OCR 结果超过容量限制。建议拆分文件后上传。",
    "ocr_invalid_response": "OCR 返回结果异常。请稍后重试，若仍失败请联系维护人员。",
    "ocr_truncated": "识别结果不完整。请检查原件，必要时拆分或重新导出该页。",
    "ocr_empty_text": "没有识别到可用文字。请检查这一页是否为空白或不清晰。",
    "ocr_blank_page": "这一页是空白页，导致整份文件无法识别。请删除空白页后重新上传。",
    "ocr_password": "文件有打开密码。请解除密码后替换文件。",
    "ocr_unsupported": "无法读取文件。请检查格式，或重新导出后替换文件。",
    "ocr_page_limit": "文档页数超过限制。请按页拆分后上传。",
    "ocr_page_mismatch": "识别结果页数与原件不符。请重新导出或按页拆分后上传。",
}


class OcrPageError(ValueError):
    """带安全分类与页码的失败；消息不含凭据、URL、原文或供应商响应。"""

    def __init__(self, category: str, page: int | None = None):
        self.category = category
        self.page = page
        super().__init__(SAFE_MESSAGES[category])

    @property
    def safe_detail(self) -> str:
        return SAFE_MESSAGES[self.category]


@dataclass(frozen=True)
class OcrPageResult:
    page: int
    raw: str
    text: str
    finish_reason: str


def ocr_purpose(extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return IMAGE_PURPOSE
    return DOCUMENT_PURPOSE


def strip_layout(raw):
    return _BOX.sub("", _REF.sub("", raw)).strip()


def _status_category(status: int) -> str:
    if status in {401, 403}:
        return "ocr_authentication"
    if status == 402:
        return "ocr_balance"
    if status == 408:
        return "ocr_upload_slow"
    if status == 413:
        return "ocr_file_too_large"
    if status == 429:
        return "ocr_rate_limited"
    if status in {500, 502, 503}:
        return "ocr_overloaded"
    if status == 504:
        return "ocr_timeout"
    return "ocr_http_error"


def _specific_error(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code") if isinstance(error.get("code"), str) else ""
    message = error.get("message") if isinstance(error.get("message"), str) else ""
    if "OCR did not finish" in message:
        return "ocr_recognition_timeout"
    if code == "gateway_timeout_error":
        return "ocr_timeout"
    if "password-protected" in message:
        return "ocr_password"
    if "1000 pages" in message:
        return "ocr_page_limit"
    if "too large" in message:
        return "ocr_file_too_large"
    if any(part in message for part in ("not supported", "could not be parsed", "could not be decoded")):
        return "ocr_unsupported"
    if "temporarily unavailable" in message or message == "Internal Server Error":
        return "ocr_overloaded"
    return None


def _loads(content: bytes):
    try:
        return json.loads(bytes(content).decode("utf-8").lstrip())
    except (UnicodeError, json.JSONDecodeError):
        return None


def _pdf_page_is_blank(content: bytes, page_number: int) -> bool:
    """仅当该页没有可提取文字、也没有图片时，才把 OCR 失败归为空白页。"""

    try:
        reader = PdfReader(io.BytesIO(content))
        if page_number < 1 or page_number > len(reader.pages):
            return False
        page = reader.pages[page_number - 1]
        if (page.extract_text() or "").strip():
            return False
        resources = page.get("/Resources")
        if resources is None:
            return True
        xobject = resources.get_object().get("/XObject")
        if xobject is None:
            return True
        return len(xobject.get_object()) == 0
    except Exception:
        return False


def _pages(payload, purpose, max_pages: int):
    if not isinstance(payload, dict) or payload.get("object") != "ocr.result" or payload.get("purpose") != purpose:
        raise OcrPageError("ocr_invalid_response")
    page_count, pages = payload.get("page_count"), payload.get("pages")
    if type(page_count) is not int or page_count < 1 or not isinstance(pages, list) or len(pages) != page_count:
        raise OcrPageError("ocr_invalid_response")
    if page_count > max_pages:
        raise OcrPageError("ocr_page_limit")
    results = []
    for expected, page in enumerate(pages):
        number = expected + 1
        if not isinstance(page, dict) or page.get("index") != expected or not isinstance(page.get("text"), str):
            raise OcrPageError("ocr_invalid_response", number)
        if page.get("finish_reason") != "stop":
            raise OcrPageError("ocr_truncated", number)
        text = strip_layout(page["text"])
        if not text:
            raise OcrPageError("ocr_empty_text", number)
        results.append(OcrPageResult(page=number, raw=page["text"], text=text, finish_reason="stop"))
    return results


async def _read_body(response, limit):
    content = bytearray()
    async for part in response.aiter_bytes():
        content.extend(part)
        if len(content) > limit:
            raise OcrPageError("ocr_response_too_large")
    return content


async def recognize_document(settings, content: bytes, extension: str):
    """以知识库导入限额识别整份文档。"""

    return await recognize_file(
        content, extension, api_key=settings.deepseek_api_key,
        max_pages=settings.knowledge_max_pdf_pages, max_chars=settings.knowledge_max_chars,
        timeout_seconds=settings.knowledge_ocr_timeout_seconds,
    )


async def recognize_file(
    content: bytes,
    extension: str,
    *,
    api_key: str,
    max_pages: int,
    max_chars: int,
    timeout_seconds: int,
    purpose: str | None = None,
) -> list[OcrPageResult]:
    """返回每一页的真实 OCR 结果；任一页截断或无正文则整份失败。"""

    part = FILE_PARTS.get(extension)
    if part is None or not content:
        raise OcrPageError("ocr_unsupported")
    if len(content) > OCR_MAX_FILE_BYTES:
        raise OcrPageError("ocr_file_too_large")
    api_key = api_key.strip()
    timeout = min(timeout_seconds, OCR_TIMEOUT_SECONDS)
    if not api_key or timeout <= 0:
        raise OcrPageError("ocr_configuration")
    purpose = purpose or ocr_purpose(extension)
    try:
        # 保活换行会触发下一次读取，但不能重置整次请求的时限。
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(
                trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(timeout, write=min(timeout, 30)),
            ) as client,
        ):
            async with client.stream(
                "POST", OCR_URL,
                headers={"Authorization": "Bearer " + api_key},
                data={"purpose": purpose}, files={"file": (part[0], content, part[1])},
            ) as response:
                body = await _read_body(response, max_chars * 6 + 100_000)
                status = response.status_code
    except OcrPageError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise OcrPageError("ocr_timeout") from None
    except httpx.RequestError:
        raise OcrPageError("ocr_transport_error") from None
    payload = _loads(body)
    specific = _specific_error(payload)
    if status != 200:
        raise OcrPageError(specific or _status_category(status))
    if payload is None or specific or (isinstance(payload, dict) and "error" in payload):
        raise OcrPageError(specific or "ocr_invalid_response")
    try:
        return _pages(payload, purpose, min(max_pages, OCR_MAX_PAGES))
    except OcrPageError as exc:
        if (
            exc.category in {"ocr_truncated", "ocr_empty_text"} and extension == ".pdf"
            and isinstance(exc.page, int)
            and await asyncio.to_thread(_pdf_page_is_blank, content, exc.page)
        ):
            raise OcrPageError("ocr_blank_page", exc.page) from None
        raise
