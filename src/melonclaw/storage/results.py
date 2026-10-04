"""读取明确交付目录；不开放任意工作区路径。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import BinaryIO

from melonclaw.storage.workspace_files import open_workspace_file, workspace_path_parts


def result_path_parts(virtual_path: str) -> tuple[str, ...]:
    """成果引用与实际文件读取共用同一套路径约束。"""
    workspace_path_parts(virtual_path)
    if not virtual_path.startswith("/outputs/"):
        raise ValueError("成果路径必须位于 /outputs/，不能包含隐藏目录或相对跳转。")
    return PurePosixPath(virtual_path).parts


def open_result_file(workspace: Path, virtual_path: str) -> BinaryIO:
    """逐层以目录 fd + NOFOLLOW 读取，避免检查后替换符号链接。"""

    result_path_parts(virtual_path)
    return open_workspace_file(workspace, virtual_path)
