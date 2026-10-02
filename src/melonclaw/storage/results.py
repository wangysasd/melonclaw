"""读取明确交付目录；不开放任意工作区路径。"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO


def open_result_file(workspace: Path, virtual_path: str) -> BinaryIO:
    """逐层以目录 fd + NOFOLLOW 读取，避免检查后替换符号链接。"""

    parts = PurePosixPath(virtual_path).parts
    if (
        not virtual_path.startswith("/outputs/")
        or len(virtual_path) > 1000
        or "\\" in virtual_path
        or any(ord(char) < 32 for char in virtual_path)
        or any(part in {".", "..", ""} or part.startswith(".") for part in virtual_path.split("/")[1:])
        or len(parts) < 3
    ):
        raise ValueError("成果路径必须位于 /outputs/，不能包含隐藏目录或相对跳转。")
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise ValueError("成果必须是普通文件。")
            handle = os.fdopen(file_descriptor, "rb")
        except BaseException:
            os.close(file_descriptor)
            raise
        return handle
    finally:
        os.close(descriptor)
