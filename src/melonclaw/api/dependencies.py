"""FastAPI 请求依赖。"""

from __future__ import annotations

from fastapi import Request

from melonclaw.services.chat import ChatService


def get_chat_service(request: Request) -> ChatService:
    """取得应用生命周期创建的 ChatService。"""

    return request.app.state.chat
