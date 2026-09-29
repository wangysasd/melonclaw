"""Skill ZIP 导入与更新：持久草稿、内容预览、版本检查、可恢复提交。"""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

import yaml

from melonclaw.repository import BusinessRepository
from melonclaw.services.skill_content import check_requirements, content_manifest, preview_content
from melonclaw.services.skill_operations import SkillOperations, write_json
from melonclaw.services.skills import (
    FRONTMATTER_RE,
    MAX_SKILL_FILE_SIZE,
    SAFE_DIRECTORY_RE,
    SkillCatalog,
    SkillDefinition,
    read_skill,
)

DRAFT_TTL_SECONDS = 15 * 60
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100
MAX_ARCHIVE_ENTRIES = 5000


class SkillImportError(ValueError):
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
    scope: str
    target_id: str | None
    base_version: int | None
    base_hash: str | None
    source_url: str
    source_ref: str
    preview: dict

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("user_id")
        result["operation"] = "update" if self.target_id else "install"
        return result


class SkillImportService:
    def __init__(self, data_root: Path) -> None:
        self.operations = SkillOperations(data_root)
        self.tmp_root = data_root / "skills" / "tmp"
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def _directory(self, draft_id: str) -> Path:
        try:
            identifier = UUID(draft_id).hex
        except ValueError as exc:
            raise SkillImportError("导入草稿标识不合法。") from exc
        return self.tmp_root / identifier

    def _sweep_expired(self) -> None:
        # 调用者持数据根锁；不根据进程内存判定其他 worker 的草稿。
        for child in self.tmp_root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            manifest = child / "draft.json"
            if manifest.is_file():
                expires = json.loads(manifest.read_text())["expires_at"]
            else:
                expires = child.stat().st_mtime + DRAFT_TTL_SECONDS
            if expires <= time.time():
                shutil.rmtree(child)

    async def _target(self, storage, user_id: str, target_id: str | None):
        context = await storage.get_user_context(user_id)
        if context is None:
            raise SkillImportError("用户不存在或租户归属无效。")
        scope = "global" if context.tenant_role in {"admin", "owner"} else "user"
        row = None
        if target_id is not None:
            row = await storage.get_skill_row(target_id)
            if (
                row is None
                or (row["scope"] == "global" and scope != "global")
                or (row["scope"] == "user" and row["created_by"] != user_id)
            ):
                raise SkillImportError("待更新技能不存在或无权更新。")
            scope = row["scope"]
        return scope, row

    async def prepare(
        self,
        *,
        user_id: str,
        archive_bytes: bytes,
        storage: BusinessRepository,
        source_type: str = "upload",
        target_id: str | None = None,
        source_url: str = "",
        source_ref: str = "",
    ) -> SkillImportDraft:
        async with self.operations.locked():
            await self.operations.recover(storage)
            self._sweep_expired()
            scope, row = await self._target(storage, user_id, target_id)
            draft_id = uuid4().hex
            directory = self._directory(draft_id)
            extracted = directory / "extracted"
            extracted.mkdir(parents=True)
            try:
                definition = self._extract_and_validate(archive_bytes, extracted)
                if row is not None and row["name"] != definition.id:
                    raise SkillImportError("更新包的 name 必须与现有技能相同。")
                if row is None:
                    await self._reject_name_conflict(storage, user_id, definition.id, scope)
                old = self.operations.target(row["storage_path"]) if row else None
                preview = preview_content(extracted / definition.id, old)
                preview["dependency_checks"] = await check_requirements(
                    preview["requirements"], storage, user_id
                )
                draft = SkillImportDraft(
                    draft_id,
                    user_id,
                    definition.id,
                    definition.display_name,
                    definition.description,
                    len(preview["files"]),
                    source_type,
                    time.time() + DRAFT_TTL_SECONDS,
                    scope,
                    target_id,
                    row["version"] if row else None,
                    content_manifest(old)[0] if old is not None and old.is_dir() else None,
                    source_url,
                    source_ref,
                    preview,
                )
                write_json(directory / "draft.json", asdict(draft))
                return draft
            except OSError as exc:
                shutil.rmtree(directory, ignore_errors=True)
                raise SkillImportError("无法读取技能包，请检查目录结构和存储权限。") from exc
            except Exception:
                shutil.rmtree(directory)
                raise

    async def confirm(
        self, *, draft_id: str, user_id: str, storage: BusinessRepository
    ) -> SkillImportDraft:
        async with self.operations.locked():
            await self.operations.recover(storage)
            draft, extracted = self._require_draft(draft_id, user_id)
            scope, row = await self._target(storage, user_id, draft.target_id)
            if scope != draft.scope:
                raise SkillImportError("安装范围已变化，请重新预览后确认。")
            if row is not None:
                old = self.operations.target(row["storage_path"])
                digest = content_manifest(old)[0] if old.is_dir() else None
                if row["version"] != draft.base_version or digest != draft.base_hash:
                    raise SkillImportError("技能内容已变化，请重新上传并查看差异。")
            else:
                await self._reject_name_conflict(storage, user_id, draft.name, scope)
            source = extracted / draft.name
            if content_manifest(source)[0] != draft.preview["content_hash"]:
                raise SkillImportError("草稿内容已变化，请重新上传。")
            path = (
                row["storage_path"]
                if row
                else (
                    f"shared/{draft.name}" if scope == "global" else f"users/{user_id}/{draft.name}"
                )
            )
            await self.operations.install(
                storage,
                source,
                row=row,
                fields={
                    "name": draft.name,
                    "scope": scope,
                    "created_by": row["created_by"] if row else user_id,
                    "storage_path": path,
                    "source_type": draft.source_type,
                    "source_url": draft.source_url,
                    "source_ref": draft.source_ref,
                    "content_hash": draft.preview["content_hash"],
                },
            )
            shutil.rmtree(extracted.parent)
            return draft

    @staticmethod
    async def _reject_name_conflict(storage, user_id: str, name: str, scope: str):
        rows = await storage.list_skill_rows_by_name(name)
        for row in rows:
            if scope == "global" and row["scope"] == "global":
                raise SkillImportError("共享技能已存在，请在卡片菜单选择更新内容。")
            if scope == "user":
                if row["scope"] == "user" and row["created_by"] == user_id:
                    raise SkillImportError("你已安装同名技能，请在卡片菜单选择更新内容。")
                if row["scope"] == "global" and row["enabled"]:
                    raise SkillImportError("与共享技能重名，请改名后重试。")

    async def cancel(self, *, draft_id: str, user_id: str):
        async with self.operations.locked():
            _, extracted = self._require_draft(draft_id, user_id)
            shutil.rmtree(extracted.parent)

    def _require_draft(self, draft_id: str, user_id: str) -> tuple[SkillImportDraft, Path]:
        directory = self._directory(draft_id)
        manifest = directory / "draft.json"
        if not manifest.is_file():
            raise SkillImportError("导入草稿不存在或已过期。")
        draft = SkillImportDraft(**json.loads(manifest.read_text()))
        if draft.user_id != user_id:
            raise SkillImportError("导入草稿不存在或已过期。")
        if draft.expires_at <= time.time():
            shutil.rmtree(directory)
            raise SkillImportError("导入草稿已过期，请重新上传。")
        return draft, directory / "extracted"

    def _extract_and_validate(self, archive_bytes: bytes, extracted: Path) -> SkillDefinition:
        """解压并做全部静态校验；失败抛 SkillImportError 并清理临时目录。"""

        if not archive_bytes:
            raise SkillImportError("上传的 ZIP 为空。")
        archive_size = len(archive_bytes)
        if archive_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise SkillImportError("上传 ZIP 超过 50MB 上限。")
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
            if archive_size > 0 and total_size / archive_size > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise SkillImportError("ZIP 压缩比异常，已拒绝。")

            # 元数据仍参与安全校验，但不参与目录识别或安装。
            files = []
            for entry in entries:
                relative = self._safe_relative_path(entry.filename)
                if entry.file_size > MAX_SKILL_FILE_SIZE:
                    raise SkillImportError(f"文件 {entry.filename!r} 超过单文件大小上限。")
                if any(
                    part in {"__MACOSX", ".DS_Store"} or part.startswith("._")
                    for part in relative.parts
                ):
                    continue
                files.append((entry, relative))
            if not files:
                raise SkillImportError("ZIP 中没有有效的 Skill 文件（仅含系统元数据）。")

            # 支持根目录直接放文件，或整体套一层顶层目录。
            top_levels = {relative.parts[0] for _, relative in files}
            has_single_wrapper = len(top_levels) == 1 and all(
                len(relative.parts) > 1 for _, relative in files
            )
            for entry, relative in files:
                if has_single_wrapper:
                    relative = Path(*relative.parts[1:])
                target = extracted / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink)

        if not (extracted / "SKILL.md").is_file():
            raise SkillImportError(
                "ZIP 中找不到顶层 SKILL.md。请将 SKILL.md 放在 ZIP 根目录，"
                "或仅包一层 Skill 目录；每次只上传一个 Skill。"
            )

        self._normalize_flat_layout(extracted)

        try:
            read_skill(next(extracted.iterdir()))
        except ValueError as exc:
            raise SkillImportError(str(exc)) from exc
        definitions = SkillCatalog(extracted).list()
        if len(definitions) != 1:
            raise SkillImportError("ZIP 必须恰好包含一个 Skill，且目录名与 frontmatter name 一致。")
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
            frontmatter = yaml.safe_load(match.group(1)) if match is not None else None
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise SkillImportError(
                "无法解析 SKILL.md frontmatter，请检查 YAML 格式与文本编码。"
            ) from exc
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
    def _safe_relative_path(filename: str) -> Path:
        normalized = filename.replace("\\", "/")
        if normalized.startswith(("/", "~")):
            raise SkillImportError(f"ZIP 包含非法路径：{filename!r}")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." or ":" in part for part in parts):
            raise SkillImportError(f"ZIP 包含非法路径：{filename!r}")
        return Path(*parts)
