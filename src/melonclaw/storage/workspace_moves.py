"""把普通会话的独享文件并入项目工作区。"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID


class WorkspaceMoveConflictError(ValueError):
    """目标已有同路径文件，或源文件不适合安全复制。"""


@dataclass
class CopiedWorkspace:
    source: Path
    created_files: list[Path] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)

    def rollback(self) -> None:
        for path in reversed(self.created_files):
            path.unlink(missing_ok=True)
        for path in reversed(self.created_dirs):
            try:
                path.rmdir()
            except OSError:
                # 其他会话可能同时向项目目录写入文件，不能递归删除它们。
                pass

    def remove_source(self) -> None:
        if self.source.is_symlink():
            raise WorkspaceMoveConflictError("普通会话工作区包含符号链接。")
        if self.source.exists():
            shutil.rmtree(self.source)


def copy_conversation_workspace(
    source: Path,
    destination: Path,
    attachment_ids: set[UUID],
) -> CopiedWorkspace:
    """复制普通文件与已登记附件；不覆盖项目里已有的任何文件。"""

    copied = CopiedWorkspace(source)
    try:
        if source.is_symlink() or destination.is_symlink():
            raise WorkspaceMoveConflictError("工作区路径包含符号链接，无法移动会话。")
        if not source.is_dir() or not destination.is_dir():
            raise WorkspaceMoveConflictError("会话或项目工作区不可用。")
        for entry in sorted(source.iterdir()):
            if entry.name == ".attachments":
                continue
            _copy_entry(entry, destination / entry.name, copied)
        source_attachments = source / ".attachments"
        destination_attachments = destination / ".attachments"
        if source_attachments.is_symlink():
            raise WorkspaceMoveConflictError("附件目录包含符号链接，无法移动会话。")
        if destination_attachments.is_symlink() or (
            destination_attachments.exists() and not destination_attachments.is_dir()
        ):
            raise WorkspaceMoveConflictError("目标项目附件目录不可用。")
        if not destination_attachments.exists():
            destination_attachments.mkdir()
            copied.created_dirs.append(destination_attachments)
        for attachment_id in sorted(attachment_ids, key=str):
            entry = source_attachments / str(attachment_id)
            if entry.exists() or entry.is_symlink():
                _copy_entry(entry, destination_attachments / str(attachment_id), copied)
        return copied
    except BaseException:
        copied.rollback()
        raise


def _copy_entry(source: Path, destination: Path, copied: CopiedWorkspace) -> None:
    if source.is_symlink():
        raise WorkspaceMoveConflictError("工作区包含符号链接，无法移动会话。")
    if source.is_dir():
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise WorkspaceMoveConflictError("目标项目中存在同名文件，请先处理后重试。")
        if not destination.exists():
            destination.mkdir()
            copied.created_dirs.append(destination)
        for child in sorted(source.iterdir()):
            _copy_entry(child, destination / child.name, copied)
        return
    if not source.is_file():
        raise WorkspaceMoveConflictError("工作区包含不支持的文件类型，无法移动会话。")
    if destination.exists() or destination.is_symlink():
        raise WorkspaceMoveConflictError("目标项目中存在同名文件，请先处理后重试。")
    # 先写临时文件，再用 hard link 发布；目标路径不会出现半份文件，也不会覆盖并发写入。
    descriptor, temporary_name = tempfile.mkstemp(prefix=".melonclaw-move-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as origin:
            shutil.copyfileobj(origin, target)
        shutil.copystat(source, temporary)
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise WorkspaceMoveConflictError("目标项目中存在同名文件，请先处理后重试。") from exc
        copied.created_files.append(destination)
    finally:
        temporary.unlink(missing_ok=True)
