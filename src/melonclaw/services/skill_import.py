"""Skill ZIP 两段式导入：prepare 解析校验生成草稿，confirm 落库生效。

安全边界（全部在 prepare 阶段执行，confirm 不再碰 zip）：
- 不调用 ``extractall``，逐 entry 做路径规范化校验后手动写盘，拒绝绝对路径、
  ``..`` 穿越、反斜杠变体；
- 压缩比、解压后总大小、单文件大小、文件数量都有硬上限；
- frontmatter 复用 SkillCatalog 的解析规则，name 与目录名必须一致；
- name 全局唯一（数据库唯一约束兜底，prepare 时提前给出友好错误）。
"""

from __future__ import annotations

import io
import logging
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from melonclaw.repository import BusinessRepository
from melonclaw.services.skills import (
    FRONTMATTER_RE,
    MAX_SKILL_FILE_SIZE,
    SAFE_DIRECTORY_RE,
    SkillCatalog,
    SkillDefinition,
)

logger = logging.getLogger(__name__)

DRAFT_TTL_SECONDS = 15 * 60
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100
MAX_ARCHIVE_ENTRIES = 5000


class SkillImportError(ValueError):
    """上传的 Skill 包不合法。"""

    status_code = 422


@dataclass
class SkillImportDraft:
    draft_id: str
    user_id: str
    name: str
    display_name: str
    description: str
    file_count: int
    source_type: str
    expires_at: float

    def public_dict(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "file_count": self.file_count,
            "expires_at": self.expires_at,
        }


class SkillImportService:
    """内存草稿表 + 临时目录；进程重启草稿即废，TTL 到期惰性清理。"""

    def __init__(self, data_root: Path) -> None:
        self.tmp_root = data_root / "skills" / "tmp"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self._drafts: dict[str, tuple[SkillImportDraft, Path]] = {}
        self._sweep_expired()

    def _sweep_expired(self) -> None:
        now = time.time()
        for draft_id, (draft, _) in list(self._drafts.items()):
            if draft.expires_at <= now:
                self._discard(draft_id)
        live = {path for _, path in self._drafts.values()}
        for child in self.tmp_root.iterdir():
            if child.is_dir() and child not in live:
                shutil.rmtree(child, ignore_errors=True)

    def _discard(self, draft_id: str) -> None:
        entry = self._drafts.pop(draft_id, None)
        if entry is not None:
            shutil.rmtree(entry[1].parent, ignore_errors=True)

    async def prepare(
        self,
        *,
        user_id: str,
        archive_bytes: bytes,
        storage: BusinessRepository,
        source_type: str = "upload",
    ) -> SkillImportDraft:
        """校验 ZIP 并解压到临时区，返回可预览的草稿。"""

        self._sweep_expired()
        draft_id = uuid4().hex
        draft_dir = self.tmp_root / draft_id
        extracted = draft_dir / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        try:
            definition = self._extract_and_validate(archive_bytes, extracted)
            existing = await storage.get_skill_row(definition.id)
            if existing is not None:
                raise SkillImportError(
                    f"技能 {definition.id!r} 已存在，请改名后重试。"
                )
            draft = SkillImportDraft(
                draft_id=draft_id,
                user_id=user_id,
                name=definition.id,
                display_name=definition.display_name,
                description=definition.description,
                file_count=sum(
                    1 for path in extracted.rglob("*") if path.is_file()
                ),
                source_type=source_type,
                expires_at=time.time() + DRAFT_TTL_SECONDS,
            )
        except Exception:
            shutil.rmtree(draft_dir, ignore_errors=True)
            raise
        self._drafts[draft_id] = (draft, extracted)
        return draft

    async def confirm(
        self,
        *,
        draft_id: str,
        user_id: str,
        storage: BusinessRepository,
    ) -> SkillImportDraft:
        """把草稿原子移动到用户 Skill 目录并落库；任何失败整体回滚。"""

        draft, extracted = self._require_draft(draft_id, user_id)
        source = extracted / draft.name
        target = self.tmp_root.parent / "users" / user_id / draft.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._discard(draft_id)
            raise SkillImportError(f"技能 {draft.name!r} 已存在。")

        await storage.create_skill_row(
            name=draft.name,
            scope="user",
            source_type=draft.source_type,
            created_by=user_id,
            storage_path=f"users/{user_id}/{draft.name}",
            enabled=False,
        )
        try:
            shutil.move(str(source), str(target))
            await storage.update_skill_row(draft.name, enabled=True)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            await storage.delete_skill_row(draft.name)
            raise
        self._drafts.pop(draft_id, None)
        shutil.rmtree(extracted.parent, ignore_errors=True)
        return draft

    async def cancel(self, *, draft_id: str, user_id: str) -> None:
        draft, _ = self._require_draft(draft_id, user_id)
        self._discard(draft.draft_id)

    def _require_draft(
        self, draft_id: str, user_id: str
    ) -> tuple[SkillImportDraft, Path]:
        entry = self._drafts.get(draft_id)
        if entry is None or entry[0].user_id != user_id:
            raise SkillImportError("导入草稿不存在或已过期。")
        if entry[0].expires_at <= time.time():
            self._discard(draft_id)
            raise SkillImportError("导入草稿已过期，请重新上传。")
        return entry

    def _extract_and_validate(
        self, archive_bytes: bytes, extracted: Path
    ) -> SkillDefinition:
        """解压并做全部静态校验；失败抛 SkillImportError 并清理临时目录。"""

        if not archive_bytes:
            raise SkillImportError("上传的 ZIP 为空。")
        archive_size = len(archive_bytes)
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        except zipfile.BadZipFile as exc:
            raise SkillImportError("上传的文件不是合法 ZIP。") from exc

        with archive:
            entries = [
                entry
                for entry in archive.infolist()
                if entry.filename and not entry.filename.endswith("/")
            ]
            if not entries:
                raise SkillImportError("ZIP 中没有文件。")
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise SkillImportError("ZIP 内文件数量超限。")
            total_size = sum(entry.file_size for entry in entries)
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                raise SkillImportError("ZIP 解压后总大小超过 50MB 上限。")
            if (
                archive_size > 0
                and total_size / archive_size > MAX_ARCHIVE_COMPRESSION_RATIO
            ):
                raise SkillImportError("ZIP 压缩比异常，已拒绝。")

            # 兼容两种打包形态：整体套一层顶层目录，或文件直接拍平在根部。
            names = [entry.filename for entry in entries]
            top_levels = {name.split("/")[0] for name in names}
            has_single_wrapper = (
                len(top_levels) == 1 and all("/" in name for name in names)
            )
            for entry in entries:
                relative = self._safe_relative_path(
                    entry.filename, strip_prefix=has_single_wrapper
                )
                if entry.file_size > MAX_SKILL_FILE_SIZE:
                    raise SkillImportError(
                        f"文件 {entry.filename!r} 超过单文件大小上限。"
                    )
                target = extracted / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink)

        if not (extracted / "SKILL.md").is_file():
            raise SkillImportError("ZIP 中找不到顶层 SKILL.md。")

        self._normalize_flat_layout(extracted)

        definitions = SkillCatalog(extracted).list()
        if len(definitions) != 1:
            raise SkillImportError(
                "ZIP 必须恰好包含一个 Skill，且目录名与 frontmatter name 一致。"
            )
        definition = definitions[0]
        if not SAFE_DIRECTORY_RE.fullmatch(definition.id):
            raise SkillImportError(f"技能名 {definition.id!r} 不合法。")
        return definition

    @staticmethod
    def _normalize_flat_layout(extracted: Path) -> None:
        """拍平形态（SKILL.md 直接在根部）归一化为 ``<name>/SKILL.md``。

        目录名取 frontmatter 的 name，与套一层顶层目录的打包形态保持同一结构。
        """

        root_skill = extracted / "SKILL.md"
        if not root_skill.is_file():
            return
        try:
            content = root_skill.read_text(encoding="utf-8")
            match = FRONTMATTER_RE.match(content)
            frontmatter = (
                yaml.safe_load(match.group(1)) if match is not None else None
            )
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise SkillImportError(f"无法解析 SKILL.md frontmatter：{exc}") from exc
        name = frontmatter.get("name") if isinstance(frontmatter, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise SkillImportError("SKILL.md frontmatter 缺少有效的 name。")
        name = name.strip()
        if not SAFE_DIRECTORY_RE.fullmatch(name):
            raise SkillImportError(f"技能名 {name!r} 不合法。")
        target = extracted / name
        target.mkdir(parents=True, exist_ok=True)
        for child in list(extracted.iterdir()):
            if child == target:
                continue
            shutil.move(str(child), str(target / child.name))

    @staticmethod
    def _safe_relative_path(filename: str, *, strip_prefix: bool) -> Path:
        normalized = filename.replace("\\", "/")
        if normalized.startswith(("/", "~")):
            raise SkillImportError(f"ZIP 包含非法路径：{filename!r}")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise SkillImportError(f"ZIP 包含非法路径：{filename!r}")
        if strip_prefix:
            parts = parts[1:]
        if not parts:
            raise SkillImportError(f"ZIP 包含非法路径：{filename!r}")
        return Path(*parts)


__all__ = [
    "DRAFT_TTL_SECONDS",
    "SkillImportDraft",
    "SkillImportError",
    "SkillImportService",
]
