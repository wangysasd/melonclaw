"""聊天生成文本包：只打包受限内容，发布仍走 SkillImportService。"""

from __future__ import annotations

import io
import re
import zipfile

from melonclaw.services.skill_import import SkillImportError

MAX_GENERATED_FILES = 32
MAX_GENERATED_BYTES = 64000
REFERENCE_PATH = re.compile(r"references/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.(?:md|txt)\Z")


def pack_generated_skill(files: dict[str, str]) -> bytes:
    """相对路径不能指定库目录；不接受脚本、二进制或任意宿主文件。"""
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_GENERATED_FILES:
        raise SkillImportError("生成包必须包含 1–32 个文本文件。")
    if "SKILL.md" not in files:
        raise SkillImportError("生成包必须包含顶层 SKILL.md。")
    entries = []
    total = 0
    for path, content in files.items():
        if not isinstance(path, str) or (path != "SKILL.md" and not REFERENCE_PATH.fullmatch(path)):
            raise SkillImportError("仅允许 SKILL.md 和 references/ 下的 .md、.txt 安全相对路径。")
        if len(path) > 240 or not isinstance(content, str) or "\x00" in content:
            raise SkillImportError("文件路径过长或正文不是有效文本。")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SkillImportError("生成正文必须是有效 UTF-8 文本。") from exc
        total += len(encoded)
        if total > MAX_GENERATED_BYTES:
            raise SkillImportError("生成包文本总大小不能超过 64000 UTF-8 字节。")
        entries.append((path, encoded))
    # 不压缩，避免模型生成的重复文本触发 ZIP 炸弹压缩比检查。
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, encoded in entries:
            archive.writestr(path, encoded)
    return buffer.getvalue()
