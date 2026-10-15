"""无人值守运行不挂载任何主机文件、项目或长期记忆。"""

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

_DENIED = "任务执行不开放文件系统；请直接返回文字结果。"


class TaskReadOnlyBackend(BackendProtocol):
    """第二层保护：即使框架内部调用 backend，也不能触及本地或 Store。

    不继承 SandboxBackendProtocol，因此框架也无法推断出 execute 能力。
    """

    def ls(self, path):
        return LsResult(error=_DENIED)

    def read(self, file_path, offset=0, limit=2000):
        return ReadResult(error=_DENIED)

    def grep(self, pattern, path=None, glob=None, *, max_count=None):
        return GrepResult(error=_DENIED)

    def glob(self, pattern, path=None):
        return GlobResult(error=_DENIED)

    def write(self, file_path, content):
        return WriteResult(error=_DENIED)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return EditResult(error=_DENIED)

    def delete(self, file_path):
        return DeleteResult(error=_DENIED)

    def upload_files(self, files):
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    def download_files(self, paths):
        return [FileDownloadResponse(path=path, error="permission_denied") for path in paths]
