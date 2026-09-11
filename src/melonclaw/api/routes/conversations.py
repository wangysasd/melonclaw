"""Conversation 查询和创建路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import ConversationRequest

router = APIRouter()


@router.post("/api/conversations", status_code=201)
async def create_conversation(
    request: Request,
    payload: ConversationRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.create_conversation(
                payload.user_id,
                payload.project_id,
                payload.tenant_id,
            ),
            status_code=201,
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/conversations")
async def list_conversations(
    request: Request,
    user_id: str,
    tenant_id: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = None,
    project_id: UUID | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        items, next_cursor = await manager.list_conversations(
            user_id,
            tenant_id=tenant_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
        )
        return JSONResponse({"items": items, "next_cursor": next_cursor})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/conversations/{conversation_id}/messages")
async def conversation_history(
    request: Request,
    conversation_id: UUID,
    user_id: str,
    tenant_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    before_seq: int | None = Query(default=None, ge=1),
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        result = await manager.history(
            conversation_id,
            user_id,
            tenant_id=tenant_id,
            limit=limit,
            before_seq=before_seq,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)
