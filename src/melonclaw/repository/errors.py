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

