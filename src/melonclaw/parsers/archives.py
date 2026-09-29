"""ZIP 附件容器校验；不解压、不读取正文、不执行内容。"""

from pathlib import Path
from zipfile import BadZipFile, ZipFile

from melonclaw.parsers.validation import AttachmentValidationError


def validate_zip(
    source: Path, *, max_entries: int, max_bytes: int, max_entry_bytes: int, max_ratio: int,
) -> None:
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > max_entries:
                raise AttachmentValidationError("ZIP 文件数量为空或超限。", "archive_too_many_entries", 413)
            names: set[str] = set()
            total = 0
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                path = Path(name)
                mode = (entry.external_attr >> 16) & 0o170000
                if (entry.orig_filename != entry.filename or path.is_absolute()
                        or ".." in path.parts or (len(name) > 1 and name[1] == ":")
                        or path.as_posix() in names or entry.flag_bits & 1
                        or mode not in {0, 0o100000, 0o040000}):
                    raise AttachmentValidationError("ZIP 包含危险或重复条目。", "invalid_zip_container", 415)
                names.add(path.as_posix())
                total += entry.file_size
                if entry.file_size > max_entry_bytes:
                    raise AttachmentValidationError("ZIP 单文件超过上限。", "archive_entry_too_large", 413)
            if total > max_bytes or total / max(source.stat().st_size, 1) > max_ratio:
                raise AttachmentValidationError("ZIP 解压大小或压缩比超限。", "archive_size_exceeded", 413)
    except BadZipFile as exc:
        raise AttachmentValidationError("文件不是合法 ZIP。", "invalid_zip_container", 415) from exc
