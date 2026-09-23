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


class ApprovalBindingError(RuntimeError):
    """审批提交没有绑定当前审批批次和助手消息。"""

    status_code = 409
    error_code = "approval_binding_required"

    def __init__(self) -> None:
        super().__init__(
            "审批提交必须绑定 approval_batch_id 和 assistant_message_id，请刷新后重试。"
        )


class UserInteractionError(Exception):
    """用户问题交互错误，携带稳定的 HTTP 状态和错误码。"""

    def __init__(self, message: str, error_code: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class UserInteractionNotFoundError(UserInteractionError):
    def __init__(self) -> None:
        super().__init__("用户问题不存在或不属于当前会话。", "user_interaction_not_found", 404)


class UserInteractionConflictError(UserInteractionError):
    def __init__(self, message: str = "用户问题状态已变化，请刷新后重试。") -> None:
        super().__init__(message, "user_interaction_conflict", 409)


class UserInteractionExpiredError(UserInteractionError):
    def __init__(self) -> None:
        super().__init__("用户问题已过期，不能继续恢复本轮执行。", "user_interaction_expired", 410)


class UserInteractionAnswerError(UserInteractionError):
    """答案格式或选项不合法。

    和 conflict 分开是为了让前端能区分「我填错了」和「这一轮已经变了」：
    前者改答案重提即可，后者必须刷新。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, "user_answer_invalid", 422)


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
        super().__init__(message, "workspace_attachment_quota_exceeded", 413)


class AttachmentStateError(AttachmentError):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message, error_code, 422)
