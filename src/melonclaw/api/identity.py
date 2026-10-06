"""附件身份从已经验证的服务端会话解析。"""
from fastapi import Request

from melonclaw.services.errors import InvalidUserError


def resolve_request_user_id(request: Request, provided: str | None) -> str:
    user_id = request.state.user.user_id
    if provided is not None and provided != user_id:
        raise InvalidUserError("当前用户已变化，请刷新页面。")
    return user_id
