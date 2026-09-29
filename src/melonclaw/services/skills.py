"""Skill 正文解析、诊断和范围身份；文件是正文唯一事实来源。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
SAFE_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_DISPLAY_NAME_LENGTH = 160
GLOBAL_SKILLS_ROUTE = "/skills/"
USER_SKILLS_ROUTE = "/skills-user/"
SKILL_SCOPES = ("global", "user")


@dataclass(frozen=True)
class SkillRoot:
    scope: str
    route_prefix: str
    directory: Path


@dataclass(frozen=True)
class SkillDefinition:
    id: str  # frontmatter name，目录内身份
    display_name: str
    description: str
    virtual_path: str
    scope: str = "global"
    resource_id: str = ""  # 仅索引解析后的定义携带数据库身份
    version: int = 1
    content_hash: str = ""

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.id}"

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "scope": self.scope,
        }

    def reference(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "resource_id": self.resource_id,
            "name": self.id,
            "scope": self.scope,
            "version": self.version,
            "content_hash": self.content_hash,
            "display_name": self.display_name,
        }


def read_skill(directory: Path) -> tuple[str, dict[str, Any]]:
    """只解析普通 UTF-8 文件；诊断不回显 YAML 正文或宿主路径。"""
    if directory.is_symlink() or not SAFE_DIRECTORY_RE.fullmatch(directory.name):
        raise ValueError("技能目录名不合法或目录是符号链接。")
    path = directory / "SKILL.md"
    if path.is_symlink():
        raise ValueError("SKILL.md 不能是符号链接。")
    if not path.is_file():
        raise ValueError("缺少 SKILL.md 文件。")
    if path.stat().st_size > MAX_SKILL_FILE_SIZE:
        raise ValueError("SKILL.md 超过 10MB 上限。")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("SKILL.md 无法读取为 UTF-8 文本。") from exc
    match = FRONTMATTER_RE.match(content)
    if match is None:
        raise ValueError("SKILL.md 缺少由 --- 包围的 frontmatter。")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError("frontmatter YAML 格式错误，请检查缩进和引号。") from exc
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter 必须是键值对象。")
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        raise ValueError("frontmatter name 必须是 1–64 字符的名称。")
    if not SKILL_NAME_RE.fullmatch(name.strip()):
        raise ValueError("技能 name 只允许小写字母、数字和单个连字符，且不能以连字符开头或结尾。")
    if name.strip() != directory.name:
        raise ValueError("frontmatter name 与技能目录名不一致。")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("frontmatter 缺少非空 description。")
    return content, metadata


class SkillCatalog:
    def __init__(self, roots: Path | Iterable[SkillRoot] | None = None) -> None:
        if isinstance(roots, Path):
            self.roots = (SkillRoot("global", GLOBAL_SKILLS_ROUTE, roots),)
        else:
            self.roots = tuple(roots) if roots is not None else ()

    def list(self) -> list[SkillDefinition]:
        definitions = []
        for root in self.roots:
            if not root.directory.is_dir():
                continue
            for directory in sorted(root.directory.iterdir()):
                if not directory.is_dir() or directory.is_symlink():
                    continue
                try:
                    content, metadata = read_skill(directory)
                except (ValueError, OSError) as exc:
                    logger.warning("跳过无效 Skill %s：%s", directory.name, type(exc).__name__)
                    continue
                definitions.append(
                    SkillDefinition(
                        id=directory.name,
                        display_name=self._display_name(metadata, content, directory.name),
                        description=metadata["description"].strip()[:MAX_SKILL_DESCRIPTION_LENGTH],
                        virtual_path=f"{root.route_prefix}{directory.name}/SKILL.md",
                        scope=root.scope,
                    )
                )
        return sorted(definitions, key=lambda item: (item.display_name.casefold(), item.key))

    def get(self, skill_id: str | None) -> SkillDefinition | None:
        return next((item for item in self.list() if item.key == skill_id), None)

    def public_items(self) -> list[dict[str, str]]:
        return [item.public_dict() for item in self.list()]

    @staticmethod
    def _display_name(metadata: dict[str, Any], content: str, name: str) -> str:
        for key in ("display_name", "title"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_SKILL_DISPLAY_NAME_LENGTH]
        for line in content.splitlines():
            if line.startswith("# "):
                heading = line[2:].strip()
                if heading and heading.casefold() != name.casefold():
                    return heading[:MAX_SKILL_DISPLAY_NAME_LENGTH]
                break
        return re.sub(r"[-_]+", " ", name).strip().title()
