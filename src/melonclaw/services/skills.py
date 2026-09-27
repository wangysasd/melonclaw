"""Skill 目录发现与安全的前端展示契约。"""

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
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_DISPLAY_NAME_LENGTH = 160

GLOBAL_SKILLS_ROUTE = "/skills/"
USER_SKILLS_ROUTE = "/skills-user/"
SKILL_SCOPES = ("global", "user", "tenant")


@dataclass(frozen=True)
class SkillRoot:
    """一个 Skill 扫描根：目录内容、scope 与对应的虚拟路径前缀。"""

    scope: str
    route_prefix: str
    directory: Path


@dataclass(frozen=True)
class SkillDefinition:
    """Skill 的安全元数据和服务端虚拟路径。"""

    id: str
    display_name: str
    description: str
    virtual_path: str
    scope: str = "global"

    def public_dict(self) -> dict[str, str]:
        """生成可以返回给浏览器的字段，不暴露宿主机路径。"""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "scope": self.scope,
        }


class SkillCatalog:
    """从若干 Skill 根目录读取 frontmatter，按 scope 打上虚拟路径。"""

    def __init__(
        self,
        roots: Path | Iterable[SkillRoot] | None = None,
    ) -> None:
        if roots is None:
            self.roots: tuple[SkillRoot, ...] = ()
        elif isinstance(roots, Path):
            self.roots = (
                SkillRoot(
                    scope="global",
                    route_prefix=GLOBAL_SKILLS_ROUTE,
                    directory=roots,
                ),
            )
        else:
            self.roots = tuple(roots)

    def list(self) -> list[SkillDefinition]:
        """列出所有根下合法的直接子目录 Skill，并按展示名稳定排序。"""

        definitions: list[SkillDefinition] = []
        for root in self.roots:
            if not root.directory.is_dir():
                continue
            for skill_dir in sorted(
                root.directory.iterdir(), key=lambda item: item.name
            ):
                if (
                    skill_dir.is_symlink()
                    or not skill_dir.is_dir()
                    or not SAFE_DIRECTORY_RE.fullmatch(skill_dir.name)
                ):
                    continue
                definition = self._parse(skill_dir, root)
                if definition is not None:
                    definitions.append(definition)
        return sorted(
            definitions,
            key=lambda item: (item.display_name.casefold(), item.id.casefold()),
        )

    def get(self, skill_id: str | None) -> SkillDefinition | None:
        """按服务端发现的 ID 查找 Skill，拒绝任意路径输入。"""

        if not skill_id or not isinstance(skill_id, str):
            return None
        return next((item for item in self.list() if item.id == skill_id), None)

    def public_items(self) -> list[dict[str, str]]:
        return [item.public_dict() for item in self.list()]

    def _parse(self, skill_dir: Path, root: SkillRoot) -> SkillDefinition | None:
        skill_path = skill_dir / "SKILL.md"
        try:
            if not skill_path.is_file() or skill_path.stat().st_size > MAX_SKILL_FILE_SIZE:
                return None
            content = skill_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("无法读取 Skill %s：%s", skill_dir.name, exc)
            return None

        match = FRONTMATTER_RE.match(content)
        if match is None:
            logger.warning("跳过没有合法 frontmatter 的 Skill：%s", skill_dir.name)
            return None
        try:
            frontmatter: Any = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            logger.warning("跳过 frontmatter 无法解析的 Skill %s：%s", skill_dir.name, exc)
            return None
        if not isinstance(frontmatter, dict):
            logger.warning("跳过 frontmatter 不是对象的 Skill：%s", skill_dir.name)
            return None

        raw_skill_id = frontmatter.get("name")
        raw_description = frontmatter.get("description")
        skill_id = raw_skill_id.strip() if isinstance(raw_skill_id, str) else ""
        description = raw_description.strip() if isinstance(raw_description, str) else ""
        if (
            not skill_id
            or len(skill_id) > MAX_SKILL_NAME_LENGTH
            or not description
        ):
            logger.warning("跳过缺少有效 name/description 的 Skill：%s", skill_dir.name)
            return None
        if skill_id != skill_dir.name:
            logger.warning(
                "跳过 name 与目录名不一致的 Skill：%s（name=%s）",
                skill_dir.name,
                skill_id,
            )
            return None

        description = description[:MAX_SKILL_DESCRIPTION_LENGTH]
        display_name = self._display_name(frontmatter, content, skill_id)

        return SkillDefinition(
            id=skill_id,
            display_name=display_name,
            description=description,
            virtual_path=f"{root.route_prefix}{skill_dir.name}/SKILL.md",
            scope=root.scope,
        )

    @staticmethod
    def _first_heading(content: str) -> str | None:
        """没有 display_name 时，用正文一级标题提供友好名称。"""

        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip() or None
        return None

    @classmethod
    def _display_name(
        cls,
        frontmatter: dict[str, Any],
        content: str,
        skill_id: str,
    ) -> str:
        """生成菜单标题，并避免把内部 slug 原样暴露给用户。"""

        for key in ("display_name", "title"):
            value = frontmatter.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_SKILL_DISPLAY_NAME_LENGTH]

        heading = cls._first_heading(content)
        if heading and heading.casefold() != skill_id.casefold():
            return heading[:MAX_SKILL_DISPLAY_NAME_LENGTH]

        humanized = re.sub(r"[-_]+", " ", skill_id).strip()
        return humanized.title()[:MAX_SKILL_DISPLAY_NAME_LENGTH] or skill_id


__all__ = [
    "GLOBAL_SKILLS_ROUTE",
    "MAX_SKILL_FILE_SIZE",
    "SAFE_DIRECTORY_RE",
    "SkillCatalog",
    "SkillDefinition",
    "SkillRoot",
    "USER_SKILLS_ROUTE",
]
