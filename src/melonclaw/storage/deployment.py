"""停服部署时复制持久数据；不覆盖、不删除、不修改数据库。"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path


def _digest(path: Path) -> bytes:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def copy_persistent_tree(source: Path, destination: Path) -> int:
    """预检全部冲突，再复制缺失项并校验；中断可重跑，源保持原样。

    调用者必须停止所有写入。拒绝符号链接、特殊文件和重叠目录；错误不输出文件内容。
    """
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("数据目录不能是符号链接。")
    if any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("目标父目录不能是符号链接。")
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("源和目标目录不能重叠。")
    if not source.is_dir():
        raise ValueError("源数据目录不存在。")
    entries = sorted(source.rglob("*"))
    if destination.exists() and not destination.is_dir():
        raise ValueError("目标不是目录。")
    for path in entries:
        target = destination / path.relative_to(source)
        if path.is_symlink() or target.is_symlink():
            raise ValueError("数据中包含符号链接，请单独处理。")
        if not path.is_file() and not path.is_dir():
            raise ValueError("数据中包含特殊文件，请单独处理。")
        if target.exists() and (
            path.is_dir() != target.is_dir()
            or (path.is_file() and _digest(path) != _digest(target))
        ):
            raise ValueError("目标存在不同内容，未覆盖；请先解决目录冲突。")
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in entries:
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
            if _digest(path) != _digest(target):
                raise ValueError("复制后校验失败，请保持停服并重新迁移。")
    return copied


def install_builtin_skills(source: Path, data_root: Path) -> int:
    """仅安装缺失的内置模板，已有同名 Skill 整体保留。"""
    if not source.is_dir():
        raise ValueError("内置模板目录不存在，请指定 --source。")
    target_root = data_root / "skills" / "shared"
    if source.resolve() == target_root.resolve():
        raise ValueError("模板目录不能与运行时目录相同。")
    installed = 0
    for template in sorted(source.iterdir()):
        if not (template / "SKILL.md").is_file():
            continue
        target = target_root / template.name
        if target.exists() or target.is_symlink():
            continue
        target_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".builtin-", dir=target_root) as temporary:
            staged = Path(temporary) / template.name
            copy_persistent_tree(template, staged)
            staged.rename(target)
        installed += 1
    return installed
