"""Project 工作区内的附件存储。

附件路径由 Project 工作区和 ``attachment_id`` 唯一推导。文件名不参与路径拼接，
原文和派生目录也不会作为静态资源暴露。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID


class LocalAttachmentStorage:
    """使用 Project 工作区 ``.attachments`` 目录的首版存储实现。"""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.root = self.workspace_dir / ".attachments"
        self.root.mkdir(parents=True, exist_ok=True)

    def attachment_dir(self, attachment_id: UUID | str) -> Path:
        candidate = (self.root / str(UUID(str(attachment_id)))).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("附件路径超出 Project 工作区。") from exc
        return candidate

    def original_path(self, attachment_id: UUID | str) -> Path:
        return self.attachment_dir(attachment_id) / "original" / "blob"

    def derived_dir(self, attachment_id: UUID | str) -> Path:
        return self.attachment_dir(attachment_id) / "derived"

    def create_temp_file(self) -> tuple[int, Path]:
        temp_dir = self.root / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="upload-", dir=temp_dir)
        return descriptor, Path(name)

    def create_parse_temp_dir(self, attachment_id: UUID | str) -> Path:
        temp_dir = self.root / ".parse-tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"{UUID(str(attachment_id))}-", dir=temp_dir))

    def publish_original(self, attachment_id: UUID | str, candidate: Path) -> None:
        destination = self.original_path(attachment_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, destination)

    def publish_derived(self, attachment_id: UUID | str, source_dir: Path) -> Path:
        destination = self.derived_dir(attachment_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        old_dir = destination.with_name("derived-old")
        if old_dir.exists():
            shutil.rmtree(old_dir)
        if destination.exists():
            os.replace(destination, old_dir)
        os.replace(source_dir, destination)
        if old_dir.exists():
            shutil.rmtree(old_dir)
        return destination

    def remove_attachment(self, attachment_id: UUID | str) -> None:
        directory = self.attachment_dir(attachment_id)
        if directory.exists():
            shutil.rmtree(directory)

    def remove_path(self, path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
