"""上传文件的扩展名、MIME、magic 和容器结构校验。"""

from __future__ import annotations

import codecs
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from filetype import guess
from PIL import Image
from pypdf import PdfReader


class AttachmentValidationError(ValueError):
    """附件无法通过同步校验。"""

    def __init__(self, message: str, code: str = "attachment_invalid", status_code: int = 422):
        super().__init__(message)
        self.error_code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AttachmentType:
    extension: str
    media_type: str
    kind: str
    parse_required: bool


SUPPORTED_TYPES: dict[str, AttachmentType] = {
    ".jpg": AttachmentType(".jpg", "image/jpeg", "image", False),
    ".jpeg": AttachmentType(".jpeg", "image/jpeg", "image", False),
    ".png": AttachmentType(".png", "image/png", "image", False),
    ".pdf": AttachmentType(".pdf", "application/pdf", "pdf", True),
    ".txt": AttachmentType(".txt", "text/plain", "text", True),
    ".md": AttachmentType(".md", "text/markdown", "text", True),
    ".csv": AttachmentType(".csv", "text/csv", "text", True),
    ".json": AttachmentType(".json", "application/json", "text", True),
    ".docx": AttachmentType(
        ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", True
    ),
    ".xlsx": AttachmentType(
        ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document", True
    ),
    ".pptx": AttachmentType(
        ".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document", True
    ),
}

_GENERIC_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


def safe_original_name(name: str | None) -> str:
    """只保留安全的展示名，不允许它影响物理路径。"""

    if not name or "\x00" in name or "/" in name or "\\" in name:
        raise AttachmentValidationError("文件名不合法。", "invalid_file_name", 400)
    if name in {".", ".."} or any(part == ".." for part in Path(name).parts):
        raise AttachmentValidationError("文件名不合法。", "invalid_file_name", 400)
    cleaned = "".join(char for char in name.strip() if char.isprintable()).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise AttachmentValidationError("文件名不合法。", "invalid_file_name", 400)
    return cleaned[:255]


def _check_declared_mime(declared: str | None, expected: str) -> None:
    value = (declared or "").split(";", 1)[0].strip().lower()
    if value not in _GENERIC_MIME_TYPES and value != expected:
        raise AttachmentValidationError("文件 MIME 类型与扩展名不一致。", "mime_mismatch", 415)


def _validate_ooxml(source: Path, spec: AttachmentType, limits: dict[str, int]) -> None:
    required = {
        ".docx": {"[Content_Types].xml", "word/document.xml"},
        ".xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
        ".pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
    }[spec.extension]
    total_uncompressed = 0
    total_compressed = 0
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > limits["archive_max_entries"]:
                raise AttachmentValidationError(
                    "Office 文件条目过多。", "archive_too_many_entries", 413
                )
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                path = Path(name.replace("\\", "/"))
                if (
                    "\x00" in name
                    or name.startswith(("/", "\\"))
                    or (len(name) > 1 and name[1] == ":")
                    or ".." in path.parts
                    or name in names
                    or entry.flag_bits & 0x1
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise AttachmentValidationError(
                        "Office 文件包含危险容器条目。", "invalid_ooxml_container", 415
                    )
                names.add(name)
                if entry.file_size > limits["archive_max_entry_bytes"]:
                    raise AttachmentValidationError(
                        "Office 文件单条目过大。", "archive_entry_too_large", 413
                    )
                if name.lower().endswith((".zip", ".jar", ".7z", ".rar", ".ole")):
                    raise AttachmentValidationError(
                        "Office 文件不允许嵌套容器。", "invalid_ooxml_container", 415
                    )
                total_uncompressed += entry.file_size
                total_compressed += entry.compress_size
            if total_uncompressed > limits["archive_max_uncompressed_bytes"]:
                raise AttachmentValidationError(
                    "Office 文件声明解压大小过大。", "archive_uncompressed_size_exceeded", 413
                )
            ratio = total_uncompressed / max(total_compressed, 1)
            if ratio > limits["archive_max_compression_ratio"]:
                raise AttachmentValidationError(
                    "Office 文件压缩比过高。", "archive_compression_ratio_exceeded", 413
                )
            if not required.issubset(names):
                raise AttachmentValidationError(
                    "Office 文件容器结构与扩展名不一致。", "invalid_ooxml_container", 415
                )
            # 只读取类型所需的文本 XML，避免把用户容器解压到工作区。
            for required_name in required:
                archive.read(required_name)
    except zipfile.BadZipFile as exc:
        raise AttachmentValidationError(
            "Office 文件不是有效的 OOXML 容器。", "invalid_ooxml_container", 415
        ) from exc


_TEXT_CHUNK_BYTES = 1024 * 1024
_JSON_STRUCTURE_MAX_BYTES = 4 * 1024 * 1024


def _validate_text_encoding(source: Path) -> None:
    """流式校验 UTF-8，不把整个文本附件读进内存。"""

    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    with source.open("rb") as handle:
        while chunk := handle.read(_TEXT_CHUNK_BYTES):
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise AttachmentValidationError(
                    "文本附件必须使用 UTF-8 编码。", "attachment_invalid"
                ) from exc
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise AttachmentValidationError(
            "文本附件必须使用 UTF-8 编码。", "attachment_invalid"
        ) from exc


def _validate_json_structure(source: Path) -> None:
    """把 JSON 结构错误前移到同步校验；超大文件只做编码校验。"""

    if source.stat().st_size > _JSON_STRUCTURE_MAX_BYTES:
        return
    try:
        with source.open(encoding="utf-8-sig") as handle:
            json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AttachmentValidationError("JSON 文件格式无效。", "attachment_invalid") from exc


def validate_attachment(
    source: Path,
    original_name: str | None,
    declared_media_type: str | None,
    *,
    image_max_pixels: int = 30_000_000,
    pdf_max_pages: int = 500,
    archive_max_entries: int = 5_000,
    archive_max_uncompressed_bytes: int = 200 * 1024 * 1024,
    archive_max_entry_bytes: int = 50 * 1024 * 1024,
    archive_max_compression_ratio: int = 50,
) -> tuple[str, str, str]:
    """返回 ``(安全文件名, 服务端 MIME, kind)``。"""

    name = safe_original_name(original_name)
    extension = Path(name).suffix.lower()
    spec = SUPPORTED_TYPES.get(extension)
    if spec is None:
        raise AttachmentValidationError("暂不支持该附件类型。", "unsupported_attachment_type", 415)
    _check_declared_mime(declared_media_type, spec.media_type)
    if source.stat().st_size == 0:
        raise AttachmentValidationError("附件不能为空。", "attachment_invalid")

    if spec.kind == "image":
        detected = guess(source)
        if detected is None or detected.mime != spec.media_type:
            raise AttachmentValidationError("图片内容与扩展名不一致。", "magic_mismatch", 415)
        try:
            with Image.open(source) as image:
                width, height = image.size
                if width * height > image_max_pixels:
                    raise AttachmentValidationError(
                        "图片像素数超过限制。", "image_pixels_exceeded", 413
                    )
                image.load()
        except AttachmentValidationError:
            raise
        except Exception as exc:  # Pillow errors are intentionally sanitized.
            raise AttachmentValidationError("图片文件损坏。", "attachment_invalid") from exc
    elif spec.kind == "pdf":
        if source.read_bytes()[:5] != b"%PDF-":
            raise AttachmentValidationError("PDF 文件头不正确。", "magic_mismatch", 415)
        try:
            reader = PdfReader(str(source), strict=False)
            if len(reader.pages) > pdf_max_pages:
                raise AttachmentValidationError("PDF 页数超过限制。", "pdf_pages_exceeded", 413)
        except AttachmentValidationError:
            raise
        except Exception as exc:
            raise AttachmentValidationError("PDF 文件损坏。", "attachment_invalid") from exc
    elif spec.kind == "document":
        _validate_ooxml(
            source,
            spec,
            {
                "archive_max_entries": archive_max_entries,
                "archive_max_uncompressed_bytes": archive_max_uncompressed_bytes,
                "archive_max_entry_bytes": archive_max_entry_bytes,
                "archive_max_compression_ratio": archive_max_compression_ratio,
            },
        )
    else:
        _validate_text_encoding(source)
        if spec.extension == ".json":
            _validate_json_structure(source)
    return name, spec.media_type, spec.kind

