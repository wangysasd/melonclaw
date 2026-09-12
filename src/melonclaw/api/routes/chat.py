"""聊天消息流路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import MessageRequest
from melonclaw.api.sse import stream_response

router = APIRouter()


@router.post(
    "/api/conversations/{conversation_id}/messages",
    response_model=None,
    response_class=StreamingResponse,
)
async def send_message(
    request: Request,
    conversation_id: UUID,
    payload: MessageRequest,
) -> JSONResponse | StreamingResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        execution = await manager.prepare_message(
            conversation_id,
            payload.user_id,
            str(payload.request_id),
            payload.content,
            model_id=payload.model_id,
            tenant_id=payload.tenant_id,
            skill_id=payload.skill_id,
        )
    except Exception as exc:  # noqa: BLE001 - 准备阶段需要真实 HTTP 状态码
        return error_response(exc)
    return stream_response(manager.stream_execution(execution))
