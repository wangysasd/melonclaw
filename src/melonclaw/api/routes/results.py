"""会话成果元信息与受控内容读取。"""

from __future__ import annotations

import asyncio
from typing import Any, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.identity import resolve_request_user_id
from melonclaw.repository import AttachmentError
from melonclaw.services.results import HTML_PREVIEW_MAX_BYTES

router = APIRouter()

HTML_RESOURCE_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; media-src data:; connect-src 'none'; "
    "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)


@router.get("/api/conversations/{conversation_id}/artifacts")
async def artifact_index(request: Request, conversation_id: UUID, user_id: str | None = None) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse({"items": await manager.results.index(
            conversation_id, resolve_request_user_id(request, user_id),
        )})
    except Exception as exc:  # noqa: BLE001 - 统一脱敏响应
        return error_response(exc)


@router.get("/api/conversations/{conversation_id}/result-files/html-preview", response_model=None)
async def html_preview(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None) -> Response:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, metadata = await manager.results.open(
            conversation_id, resolve_request_user_id(request, user_id), path,
        )
        return await html_preview_response(handle, metadata)
    except Exception as exc:  # noqa: BLE001 - 统一脱敏响应
        return error_response(exc)


@router.get("/api/conversations/{conversation_id}/result-files")
async def result_metadata(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, metadata = await manager.results.open(conversation_id, resolve_request_user_id(request, user_id), path)
        handle.close()
        return JSONResponse(metadata)
    except Exception as exc:  # noqa: BLE001 - 统一脱敏响应
        return error_response(exc)


@router.get("/api/conversations/{conversation_id}/result-files/content", response_model=None)
async def result_content(request: Request, conversation_id: UUID, path: str = Query(max_length=1000), user_id: str | None = None, preview: bool = False) -> StreamingResponse | JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        handle, metadata = await manager.results.open(conversation_id, resolve_request_user_id(request, user_id), path)
        return file_content_response(handle, metadata, preview)
    except Exception as exc:  # noqa: BLE001 - 统一脱敏响应
        return error_response(exc)


async def html_preview_response(handle: BinaryIO, metadata: dict[str, Any]) -> Response:
    try:
        if metadata["preview_kind"] != "html":
            raise AttachmentError("HTML 预览仅支持不超过 2 MB 的 UTF-8 文件，请下载查看。", "html_preview_unsupported", 422)
        content = await asyncio.to_thread(handle.read, HTML_PREVIEW_MAX_BYTES + 1)
        if len(content) > HTML_PREVIEW_MAX_BYTES:
            raise AttachmentError("HTML 文件已变更或超过预览上限，请下载查看。", "html_preview_too_large", 413)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentError("HTML 文件不是有效 UTF-8，请下载查看。", "html_preview_invalid", 422) from exc
    finally:
        handle.close()
    # Blob 预览不保留 HTTP CSP。先加入可信 meta 策略，使 Blob 内的文档
    # 也受限制；sandbox 由响应头和前端 iframe 属性分别强制。
    prefix = f'<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{HTML_RESOURCE_POLICY}"><!--melonclaw-preview-source-->'.encode()
    return Response(prefix + content, media_type="text/html", headers={
        "Content-Security-Policy": HTML_RESOURCE_POLICY + "; sandbox allow-scripts",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()",
    })


def file_content_response(handle: BinaryIO, metadata: dict[str, Any], preview: bool) -> StreamingResponse:
    def chunks():
        try:
            while data := handle.read(64 * 1024):
                yield data
        finally:
            handle.close()
    disposition = "inline" if preview and metadata["preview_kind"] in {"image", "text", "pdf"} else "attachment"
    return StreamingResponse(
        chunks(), media_type=metadata["media_type"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(metadata['file_name'], safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(handle.close),
    )
