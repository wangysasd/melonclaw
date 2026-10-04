"""模型重试与执行失败分类；不暴露供应商响应或凭据。"""

import httpx
import openai
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.exceptions import ContextOverflowError
from langchain_core.tools import ToolException

from melonclaw.core.chat_model import ModelInvocationError


def retry_transient_model_error(exc: Exception) -> bool:
    """仅重试瞬时网络/限流/网关错误，输出过任何片段就停止重试。"""
    if isinstance(exc, ModelInvocationError):
        if exc.partial_output:
            return False
        exc = exc.__cause__ or exc
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError,
                        httpx.TimeoutException, httpx.NetworkError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code in {429, 500, 502, 503, 504}


def execution_error_code(exc: Exception) -> str:
    if isinstance(exc, (ModelCallLimitExceededError, ToolCallLimitExceededError)):
        return "agent_call_limit_exceeded"
    if isinstance(exc, (ModelInvocationError, ContextOverflowError)):
        return "model_execution_failed"
    return "tool_execution_failed" if isinstance(exc, ToolException) else "agent_execution_failed"
