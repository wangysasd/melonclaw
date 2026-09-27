"""应用业务服务异常。"""

from __future__ import annotations


class InvalidUserError(ValueError):
    """请求中的用户不存在或没有有效的唯一租户归属。"""


class RequestInProgressError(RuntimeError):
    """同一个 request_id 已经在执行中。"""


class AgentExecutionError(RuntimeError):
    """Agent 没有产生可以持久化的最终回复。

    默认映射为 500：这类失败多数是不可恢复的服务端问题。但有些失败其实
    由用户操作就能纠正（例如已选技能在执行开始前被移除），调用方传
    ``status_code`` 覆盖，避免前端把可纠正状态当服务端故障去重试。
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
