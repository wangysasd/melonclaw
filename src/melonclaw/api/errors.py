"""Web 异常到安全 HTTP 响应的转换。"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from melonclaw.database import (
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
)
from melonclaw.output.formatting import sanitize_text
from melonclaw.repository import (
    AssistantStateConflictError,
    ConversationBusyError,
    ConversationNotFoundError,
    ProjectNotFoundError,
    RequestConflictError,
)
from melonclaw.services.errors import InvalidUserError, RequestInProgressError


def error_response(exc: Exception) -> JSONResponse:
    """把业务异常转换为不泄露内部细节的 JSON 错误。"""

    if isinstance(exc, (ConversationNotFoundError, ProjectNotFoundError)):
        status_code = 404
    elif isinstance(exc, InvalidUserError):
        status_code = 400
    elif isinstance(
        exc,
        (
            ConversationBusyError,
            AssistantStateConflictError,
            RequestConflictError,
            RequestInProgressError,
        ),
    ):
        status_code = 409
    elif isinstance(
        exc,
        (DatabaseConfigurationError, DatabaseSchemaError, DatabaseUnavailableError),
    ):
        status_code = 503
    elif isinstance(exc, ValueError):
        status_code = 400
    else:
        status_code = 500
    return JSONResponse(
        {"error": sanitize_text(str(exc)) or "请求处理失败。"},
        status_code=status_code,
    )
