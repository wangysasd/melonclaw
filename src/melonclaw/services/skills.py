"""项目 Skill 目录发现与安全的前端展示契约。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_SKILLS_DIR = PROJECT_ROOT / "skills"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
SAFE_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_DISPLAY_NAME_LENGTH = 160


@dataclass(frozen=True)
class SkillDefinition:
    """Skill 的安全元数据和服务端虚拟路径。"""

    id: str
    display_name: str
    description: str
    virtual_path: str

    def public_dict(self) -> dict[str, str]:
        """生成可以返回给浏览器的字段，不暴露宿主机路径。"""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
        }


class SkillCatalog:
    """从项目 ``skills/`` 目录读取 Skill frontmatter。"""

    def __init__(self, skills_dir: Path = PROJECT_SKILLS_DIR) -> None:
        self.skills_dir = skills_dir

    def list(self) -> list[SkillDefinition]:
        """列出合法的直接子目录 Skill，并按展示名稳定排序。"""

        if not self.skills_dir.is_dir():
            return []

        definitions: list[SkillDefinition] = []
        for skill_dir in sorted(self.skills_dir.iterdir(), key=lambda item: item.name):
            if (
                skill_dir.is_symlink()
                or not skill_dir.is_dir()
                or not SAFE_DIRECTORY_RE.fullmatch(skill_dir.name)
            ):
                continue
            definition = self._parse(skill_dir)
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

    def _parse(self, skill_dir: Path) -> SkillDefinition | None:
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
            virtual_path=f"/skills/{skill_dir.name}/SKILL.md",
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
            # 当前仓库的部分历史 Skill 标题以 ``skill`` 结尾；它是实现后缀，
            # 对用户菜单没有信息增益。
            display_name = re.sub(
                r"\s*skill\s*$",
                "",
                heading,
                flags=re.IGNORECASE,
            ).strip()
            if display_name:
                return display_name[:MAX_SKILL_DISPLAY_NAME_LENGTH]

        humanized = re.sub(r"[-_]+", " ", skill_id).strip()
        return humanized.title()[:MAX_SKILL_DISPLAY_NAME_LENGTH] or skill_id


skill_catalog = SkillCatalog()


__all__ = ["SkillCatalog", "SkillDefinition", "skill_catalog"]
