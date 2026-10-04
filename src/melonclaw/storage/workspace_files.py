"""工作区普通目录／文件的只读访问；逐层 fd 打开，不跟随符号链接。"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

DIRECTORY_MAX_ENTRIES = 5000


def workspace_path_parts(path: str, *, directory: bool = False) -> tuple[str, ...]:
    if directory and path == "/":
        return ()
    if (
        not path.startswith("/") or len(path) > 1000 or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(not part or part.startswith(".") for part in path.split("/")[1:])
    ):
        raise ValueError("路径必须位于当前工作区，不能包含隐藏目录或相对跳转。")
    return tuple(path.split("/")[1:])


def _directory_fd(workspace: Path, parts: tuple[str, ...]) -> int:
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_workspace_file(workspace: Path, path: str) -> BinaryIO:
    parts = workspace_path_parts(path)
    descriptor = _directory_fd(workspace, parts[:-1])
    try:
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise ValueError("只能读取工作区中的普通文件。")
            return os.fdopen(file_descriptor, "rb")
        except BaseException:
            os.close(file_descriptor)
            raise
    finally:
        os.close(descriptor)


def list_workspace_directory(workspace: Path, path: str, query: str, sort: str, offset: int, limit: int) -> dict[str, Any]:
    descriptor = _directory_fd(workspace, workspace_path_parts(path, directory=True))
    entries = []
    try:
        with os.scandir(descriptor) as scanned:
            for count, entry in enumerate(scanned, 1):
                if count > DIRECTORY_MAX_ENTRIES:
                    raise OverflowError("当前目录文件过多，请将文件分组到子目录后浏览。")
                child_path = f"{path.rstrip('/')}/{entry.name}"
                try:
                    workspace_path_parts(child_path)
                    info = entry.stat(follow_symlinks=False)
                except (ValueError, OSError):
                    continue
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    continue
                if query.casefold() not in entry.name.casefold():
                    continue
                entries.append({
                    "name": entry.name, "path": child_path,
                    "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                    "size_bytes": None if stat.S_ISDIR(info.st_mode) else info.st_size,
                    "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                })
    finally:
        os.close(descriptor)
    entries.sort(key=lambda item: (
        item["kind"] != "directory",
        -datetime.fromisoformat(item["modified_at"]).timestamp() if sort == "modified" else item["name"].casefold(),
        item["name"],
    ))
    end = offset + limit
    return {"path": path, "items": entries[offset:end], "total": len(entries),
            "next_offset": end if end < len(entries) else None}
