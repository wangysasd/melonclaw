"""HITL 审批恢复路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import ApprovalRequest
from melonclaw.api.sse import stream_response

router = APIRouter()


@router.post(
    "/api/conversations/{conversation_id}/approval",
    response_model=None,
    response_class=StreamingResponse,
)
async def submit_approval(
    request: Request,
    conversation_id: UUID,
    payload: ApprovalRequest,
) -> JSONResponse | StreamingResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        execution, command = await manager.prepare_approval(
            conversation_id,
            payload.user_id,
            payload.decisions,
            payload.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - 准备阶段需要真实 HTTP 状态码
        return error_response(exc)
    return stream_response(manager.stream_execution(execution, agent_input=command))
