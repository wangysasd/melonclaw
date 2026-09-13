"""业务仓储异常定义。"""

from __future__ import annotations


class ConversationNotFoundError(LookupError):
    """会话不存在或不属于请求中的 user_id。"""

    def __init__(self) -> None:
        super().__init__("会话不存在或不属于当前用户。")


class ProjectNotFoundError(LookupError):
    """Project 不存在或不属于请求中的用户。"""

    def __init__(self) -> None:
        super().__init__("Project 不存在或不属于当前用户。")


class ConversationBusyError(RuntimeError):
    """同一会话已有另一个 Agent 执行。"""


class AssistantStateConflictError(RuntimeError):
    """助手消息已经离开预期状态，拒绝旧执行覆盖新状态。"""


class RequestConflictError(RuntimeError):
    """相同 request_id 的正文与第一次请求不一致。"""


class AttachmentError(Exception):
    """附件用例错误，携带稳定的 HTTP 错误码。"""

    def __init__(self, message: str, error_code: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class AttachmentNotFoundError(AttachmentError):
    def __init__(self) -> None:
        super().__init__("附件不存在或不属于当前资源。", "attachment_not_found", 404)


class AttachmentInUseError(AttachmentError):
    def __init__(self) -> None:
        super().__init__("附件已被消息引用，不能删除。", "attachment_in_use", 409)


class AttachmentConflictError(AttachmentError):
    def __init__(self) -> None:
        super().__init__("相同上传请求对应的文件指纹不同。", "upload_request_conflict", 409)


class AttachmentQuotaError(AttachmentError):
    def __init__(self, message: str = "附件配额已超限。") -> None:
        super().__init__(message, "project_attachment_quota_exceeded", 413)


class AttachmentStateError(AttachmentError):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message, error_code, 422)
