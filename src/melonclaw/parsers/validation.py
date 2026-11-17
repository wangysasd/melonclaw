"""上传文件的扩展名、MIME、magic 和容器结构校验。"""

from __future__ import annotations

import codecs
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import olefile
import xlrd
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
    ocr_required: bool = False


@dataclass(frozen=True)
class ValidatedAttachment:
    name: str
    media_type: str
    kind: str
    page_count: int | None = None
    first_frame_only: bool = False


SUPPORTED_TYPES: dict[str, AttachmentType] = {
    ".jpg": AttachmentType(".jpg", "image/jpeg", "image"),
    ".jpeg": AttachmentType(".jpeg", "image/jpeg", "image"),
    ".png": AttachmentType(".png", "image/png", "image"),
    ".webp": AttachmentType(".webp", "image/webp", "image"),
    ".gif": AttachmentType(".gif", "image/gif", "image"),
    ".bmp": AttachmentType(".bmp", "image/bmp", "image"),
    ".tif": AttachmentType(".tif", "image/tiff", "image"),
    ".tiff": AttachmentType(".tiff", "image/tiff", "image"),
    ".zip": AttachmentType(".zip", "application/zip", "archive"),
    ".pdf": AttachmentType(".pdf", "application/pdf", "pdf", True),
    ".txt": AttachmentType(".txt", "text/plain", "text"),
    ".md": AttachmentType(".md", "text/markdown", "text"),
    ".csv": AttachmentType(".csv", "text/csv", "text"),
    ".json": AttachmentType(".json", "application/json", "text"),
    ".doc": AttachmentType(".doc", "application/msword", "document", True),
    ".docx": AttachmentType(
        ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", True
    ),
    ".xls": AttachmentType(".xls", "application/vnd.ms-excel", "document"),
    ".xlsx": AttachmentType(
        ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document"
    ),
    ".ppt": AttachmentType(".ppt", "application/vnd.ms-powerpoint", "document", True),
    ".pptx": AttachmentType(
        ".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document", True
    ),
}

_GENERIC_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_MIME_ALIASES = {
    "application/zip": {"application/x-zip-compressed"},
    "image/bmp": {"image/x-bmp", "image/x-ms-bmp"},
}
_IMAGE_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
    "image/bmp": "BMP",
    "image/tiff": "TIFF",
}
_OFFICE_STREAMS = {
    ".doc": ("WordDocument",),
    ".ppt": ("PowerPoint Document",),
    ".xls": ("Workbook", "Book"),
}


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
    if value not in _GENERIC_MIME_TYPES | {expected} | _MIME_ALIASES.get(expected, set()):
        raise AttachmentValidationError("文件 MIME 类型与扩展名不一致。", "mime_mismatch", 415)


def _validate_legacy_office(source: Path, extension: str) -> None:
    try:
        if olefile.isOleFile(source):
            with olefile.OleFileIO(source, raise_defects=olefile.DEFECT_INCORRECT) as container:
                if any(
                    container.exists(name) and container.get_type(name) == olefile.STGTY_STREAM
                    for name in _OFFICE_STREAMS[extension]
                ):
                    return
        elif extension == ".xls" and xlrd.inspect_format(path=str(source)) == "xls":
            return
    except (OSError, ValueError):
        pass
    raise AttachmentValidationError(
        "Office 文件内容与扩展名不一致。", "invalid_office_container", 415
    )


def _validate_ooxml(source: Path, spec: AttachmentType, limits: dict[str, int]) -> int | None:
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
            pages = None
            for required_name in required:
                data = archive.read(required_name)
                if required_name == "ppt/presentation.xml":
                    pages = len(ElementTree.fromstring(data).findall("./{*}sldIdLst/{*}sldId"))
            return pages
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
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
    ocr_max_pages: int = 1000,
    archive_max_entries: int = 5_000,
    archive_max_uncompressed_bytes: int = 200 * 1024 * 1024,
    archive_max_entry_bytes: int = 50 * 1024 * 1024,
    archive_max_compression_ratio: int = 50,
) -> ValidatedAttachment:
    """检查原件并返回服务端判定的格式、页数和首帧策略。"""

    name = safe_original_name(original_name)
    extension = Path(name).suffix.lower()
    spec = SUPPORTED_TYPES.get(extension)
    if spec is None:
        raise AttachmentValidationError("暂不支持该附件类型。", "unsupported_attachment_type", 415)
    _check_declared_mime(declared_media_type, spec.media_type)
    if source.stat().st_size == 0:
        raise AttachmentValidationError("附件不能为空。", "attachment_invalid")

    kind = spec.kind
    pages = None
    first_frame_only = False
    if kind == "image":
        try:
            with Image.open(source) as image:
                if image.format != _IMAGE_FORMATS[spec.media_type]:
                    raise AttachmentValidationError("图片内容与扩展名不一致。", "magic_mismatch", 415)
                pages = image.n_frames if image.format == "TIFF" else 1
                if pages > ocr_max_pages:
                    raise AttachmentValidationError("TIFF 页数超过限制。", "ocr_page_limit", 413)
                if pages > 1:
                    kind = "document"
                first_frame_only = image.format in {"GIF", "WEBP"} and image.is_animated
                for index in range(pages):
                    image.seek(index)
                    if image.width * image.height > image_max_pixels:
                        raise AttachmentValidationError(
                            "图片像素数超过限制。", "image_pixels_exceeded", 413
                        )
                image.seek(0)
                image.load()
        except AttachmentValidationError:
            raise
        except Exception as exc:  # Pillow errors are intentionally sanitized.
            raise AttachmentValidationError("图片文件损坏。", "attachment_invalid") from exc
    elif kind == "pdf":
        with source.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise AttachmentValidationError("PDF 文件头不正确。", "magic_mismatch", 415)
        try:
            reader = PdfReader(str(source), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise AttachmentValidationError("文件有打开密码，请解除密码后上传。", "ocr_password")
            pages = len(reader.pages)
            if pages > pdf_max_pages:
                raise AttachmentValidationError("PDF 页数超过限制。", "pdf_pages_exceeded", 413)
        except AttachmentValidationError:
            raise
        except Exception as exc:
            raise AttachmentValidationError("PDF 文件损坏。", "attachment_invalid") from exc
    elif kind == "archive":
        from melonclaw.parsers.archives import validate_zip

        validate_zip(
            source, max_entries=archive_max_entries,
            max_bytes=archive_max_uncompressed_bytes,
            max_entry_bytes=archive_max_entry_bytes,
            max_ratio=archive_max_compression_ratio,
        )
    elif extension in _OFFICE_STREAMS:
        _validate_legacy_office(source, extension)
    elif kind == "document":
        pages = _validate_ooxml(
            source,
            spec,
            {
                "archive_max_entries": archive_max_entries,
                "archive_max_uncompressed_bytes": archive_max_uncompressed_bytes,
                "archive_max_entry_bytes": archive_max_entry_bytes,
                "archive_max_compression_ratio": archive_max_compression_ratio,
            },
        )
        if pages is not None and pages > ocr_max_pages:
            raise AttachmentValidationError("演示文稿页数超过限制。", "ocr_page_limit", 413)
    else:
        _validate_text_encoding(source)
        if spec.extension == ".json":
            _validate_json_structure(source)
    return ValidatedAttachment(name, spec.media_type, kind, pages, first_frame_only)

