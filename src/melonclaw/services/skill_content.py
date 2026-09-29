"""Skill 包内容摘要、预览和声明式依赖检查；绝不执行包内代码。"""

from __future__ import annotations

import difflib
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from melonclaw.services.skills import MAX_SKILL_FILE_SIZE, read_skill

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILES = 5000
PREVIEW_CHARACTERS = 64000


def content_manifest(directory: Path) -> tuple[str, list[dict[str, Any]]]:
    files = []
    total = 0
    digest = hashlib.sha256()
    if directory.is_symlink():
        raise ValueError("技能目录不能是符号链接。")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("技能包不能包含符号链接。")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("技能包只能包含普通文件。")
        size = path.stat().st_size
        total += size
        if (
            size > MAX_SKILL_FILE_SIZE
            or total > MAX_PACKAGE_BYTES
            or len(files) >= MAX_PACKAGE_FILES
        ):
            raise ValueError("技能包超过文件大小、总大小或文件数量上限。")
        name = path.relative_to(directory).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{name}\0{size}\0{file_hash}\n".encode())
        files.append({"path": name, "size": size, "sha256": file_hash})
    return digest.hexdigest(), files


def preview_content(directory: Path, old: Path | None = None) -> dict[str, Any]:
    content, metadata = read_skill(directory)
    digest, files = content_manifest(directory)
    old_files: list[dict[str, Any]] = []
    old_content = ""
    if old is not None and old.is_dir():
        _, old_files = content_manifest(old)
        try:
            old_content, _ = read_skill(old)
        except ValueError:
            pass  # 损坏的正文可通过更新修复，文件清单仍参与差异比较。
    before = {item["path"]: item["sha256"] for item in old_files}
    after = {item["path"]: item["sha256"] for item in files}
    changes = {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "modified": sorted(
            key for key in before.keys() & after.keys() if before[key] != after[key]
        ),
    }
    # 截断后再 diff，避免巨大文本占用无界 CPU/内存。
    diff = "".join(
        difflib.unified_diff(
            old_content[:PREVIEW_CHARACTERS].splitlines(keepends=True),
            content[:PREVIEW_CHARACTERS].splitlines(keepends=True),
            fromfile="原 SKILL.md",
            tofile="新 SKILL.md",
        )
    )[:PREVIEW_CHARACTERS]
    return {
        "content_hash": digest,
        "files": files,
        "body": content[:PREVIEW_CHARACTERS],
        "body_truncated": len(content) > PREVIEW_CHARACTERS,
        "diff": diff,
        "changes": changes,
        "requirements": parse_requirements(metadata),
    }


def parse_requirements(metadata: dict[str, Any]) -> dict[str, list[str]]:
    requirements = metadata.get("melonclaw_requirements", {})
    if not isinstance(requirements, dict) or set(requirements) - {"commands", "mcp", "config"}:
        raise ValueError("melonclaw_requirements 仅支持 commands、mcp、config 三类名称列表。")
    result = {}
    for kind in ("commands", "mcp", "config"):
        values = requirements.get(kind, [])
        if (
            not isinstance(values, list)
            or len(values) > 32
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", value)
                for value in values
            )
        ):
            raise ValueError(f"melonclaw_requirements.{kind} 必须是至多 32 个安全名称。")
        result[kind] = values
    return result


async def check_requirements(requirements, storage, user_id: str) -> list[dict[str, str]]:
    results = []
    mcp_names = set()
    if requirements["mcp"]:
        mcp_names = {row["slug"] for row in await storage.list_visible_mcp_rows(user_id)}
    for kind, names in requirements.items():
        for name in names:
            if kind == "commands":
                status = "available" if shutil.which(name) else "missing"
            elif kind == "mcp":
                status = "available" if name in mcp_names else "missing"
            else:
                # 不探测应用环境变量，避免把服务器凭据是否存在变成用户探针。
                status = "manual"
            results.append({"kind": kind, "name": name, "status": status})
    return results
