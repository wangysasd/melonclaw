"""Conversation 查询和创建路由。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import (
    ConversationMoveRequest,
    ConversationRequest,
    ResourceUpdateRequest,
)

router = APIRouter()


@router.post("/api/conversations/{conversation_id}/move-to-project")
async def move_conversation_to_project(
    request: Request, conversation_id: UUID, payload: ConversationMoveRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        moved = await manager.move_conversation_to_project(
            conversation_id, payload.project_id, payload.user_id,
        )
        return JSONResponse(moved)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.patch("/api/conversations/{conversation_id}")
async def update_conversation(request: Request, conversation_id: UUID, payload: ResourceUpdateRequest) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        if payload.name is None and payload.is_pinned is None:
            raise ValueError("请提供名称或置顶状态。")
        return JSONResponse(await manager.update_conversation(
            conversation_id, payload.user_id,
            title=payload.name, is_pinned=payload.is_pinned,
        ))
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(request: Request, conversation_id: UUID, user_id: str) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.update_conversation(conversation_id, user_id, delete=True)
        return JSONResponse({"deleted": True})
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


@router.post("/api/conversations", status_code=201)
async def create_conversation(
    request: Request,
    payload: ConversationRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.create_conversation(
                payload.user_id,
                payload.project_id,
            ),
            status_code=201,
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/conversations")
async def list_conversations(
    request: Request,
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = None,
    project_id: UUID | None = None,
    scope: Literal["unassigned"] | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    if scope is not None and project_id is not None:
        raise HTTPException(422, "scope 与 project_id 不能同时指定。")
    try:
        items, next_cursor = await manager.list_conversations(
            user_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
            scope=scope,
        )
        return JSONResponse({"items": items, "next_cursor": next_cursor})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/conversations/{conversation_id}/messages")
async def conversation_history(
    request: Request,
    conversation_id: UUID,
    user_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before_seq: int | None = Query(default=None, ge=1),
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        result = await manager.history(
            conversation_id,
            user_id,
            limit=limit,
            before_seq=before_seq,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)
