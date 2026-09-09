"""应用业务服务异常。"""

from __future__ import annotations


class InvalidUserError(ValueError):
    """请求中的用户不存在或没有有效的租户标签。"""


class RequestInProgressError(RuntimeError):
    """同一个 request_id 已经在执行中。"""


class AgentExecutionError(RuntimeError):
    """Agent 没有产生可以持久化的最终回复。"""
