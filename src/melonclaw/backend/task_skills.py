"""当前有效 Skill 的只读文件视图；不提供主机、工作区或执行能力。"""

import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from deepagents.backends.protocol import FileData, FileDownloadResponse, LsResult, ReadResult
from deepagents.backends.utils import slice_read_response

from melonclaw.backend.task_readonly import TaskReadOnlyBackend

_MAX_BYTES = 10 * 1024 * 1024
_UNAVAILABLE = "当前 Skill 文件不可用或无权读取；请跳过相关环节并说明。"


class TaskSkillBackend(TaskReadOnlyBackend):
    """只映射服务端批准的单个 Skill；逐层 O_NOFOLLOW 防止符号链接逃逸。"""

    def __init__(self, mounts: tuple[tuple[str, Path], ...]):
        self.mounts = {
            prefix: directory.absolute()
            for prefix, directory in mounts
        }
        self.sources = sorted({str(PurePosixPath(prefix).parent) + "/" for prefix in self.mounts})

    @staticmethod
    def _path(path):
        if not path.startswith("/") or "\\" in path or "\0" in path:
            raise ValueError("无效虚拟路径")
        if any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("无效虚拟路径")
        return str(PurePosixPath(path))

    @contextmanager
    def _open(self, path, *, directory=False):
        virtual = self._path(path)
        for prefix, root in self.mounts.items():
            if virtual == prefix.rstrip("/") or virtual.startswith(prefix):
                relative = virtual[len(prefix.rstrip("/")):].lstrip("/")
                parts = (*root.parts[1:], *PurePosixPath(relative).parts)
                break
        else:
            raise ValueError("路径未授权")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index < len(parts) - 1 or directory:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def _bytes(self, path):
        with self._open(path) as fd:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                raise ValueError("文件类型或大小不允许")
            with os.fdopen(os.dup(fd), "rb") as stream:
                content = stream.read(_MAX_BYTES + 1)
            if len(content) > _MAX_BYTES:
                raise ValueError("文件过大")
            return content

    def ls(self, path):
        try:
            virtual = self._path(path)
            if virtual + "/" in self.sources:
                return LsResult(entries=[
                    {"path": prefix, "is_dir": True}
                    for prefix in self.mounts if str(PurePosixPath(prefix).parent) == virtual
                ])
            with self._open(virtual, directory=True) as fd:
                entries = []
                with os.scandir(fd) as children:
                    for index, child in enumerate(children):
                        if index >= 5000:
                            return LsResult(error="目录条目过多，请缩小读取范围。")
                        if child.is_symlink():
                            continue
                        is_dir = child.is_dir(follow_symlinks=False)
                        if is_dir or child.is_file(follow_symlinks=False):
                            entries.append({"path": f"{virtual}/{child.name}" + ("/" if is_dir else ""),
                                            "is_dir": is_dir})
                return LsResult(entries=sorted(entries, key=lambda entry: entry["path"]))
        except (OSError, ValueError, RuntimeError):
            return LsResult(error=_UNAVAILABLE)

    def read(self, file_path, offset=0, limit=2000):
        try:
            content = self._bytes(file_path).decode("utf-8")
            return slice_read_response(FileData(content=content, encoding="utf-8"), offset, limit)
        except (OSError, ValueError, RuntimeError):
            return ReadResult(error=_UNAVAILABLE)

    def download_files(self, paths):
        # SkillsMiddleware 通过此协议读取 SKILL.md；不向 Agent 注册下载工具。
        responses = []
        for path in paths:
            try:
                responses.append(FileDownloadResponse(path=path, content=self._bytes(path)))
            except (OSError, ValueError, RuntimeError):
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
        return responses
