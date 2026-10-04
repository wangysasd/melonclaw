"""当前会话实际工作区的只读目录、附件索引与文件内容。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.identity import resolve_request_user_id
from melonclaw.api.routes.results import file_content_response, html_preview_response

router = APIRouter(prefix="/api/conversations/{conversation_id}/files")


@router.get("")
async def directory(
    request: Request, conversation_id: UUID, user_id: str | None = None,
    path: str = Query(default="/", max_length=1000), q: str = Query(default="", max_length=200),
    sort: Literal["name", "modified"] = "name", offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.results.directory(
            conversation_id, resolve_request_user_id(request, user_id), path, q, sort, offset, limit,
        ))
    except Exception as exc:  # noqa: BLE001 - 脱敏业务错误
        return error_response(exc)


@router.get("/attachments")
async def attachments(
    request: Request, conversation_id: UUID, user_id: str | None = None,
    q: str = Query(default="", max_length=200), sort: Literal["name", "modified"] = "name",
    offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200),
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.results.attachments(
            conversation_id, resolve_request_user_id(request, user_id), q, sort, offset, limit,
        ))
    except Exception as exc:  # noqa: BLE001 - 脱敏业务错误
        return error_response(exc)


@router.get("/metadata")
async def metadata(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, info = await manager.results.open_workspace(conversation_id, resolve_request_user_id(request, user_id), path)
        handle.close()
        return JSONResponse(info)
    except Exception as exc:  # noqa: BLE001 - 脱敏业务错误
        return error_response(exc)


@router.get("/content", response_model=None)
async def content(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None, preview: bool = False) -> Response:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, info = await manager.results.open_workspace(conversation_id, resolve_request_user_id(request, user_id), path)
        return file_content_response(handle, info, preview)
    except Exception as exc:  # noqa: BLE001 - 脱敏业务错误
        return error_response(exc)


@router.get("/html-preview", response_model=None)
async def html_preview(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None) -> Response:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, info = await manager.results.open_workspace(conversation_id, resolve_request_user_id(request, user_id), path)
        return await html_preview_response(handle, info)
    except Exception as exc:  # noqa: BLE001 - 脱敏业务错误
        return error_response(exc)
