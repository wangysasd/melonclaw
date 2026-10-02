"""会话成果元信息与受控内容读取。"""

from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.identity import resolve_request_user_id

router = APIRouter()


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
        def chunks():
            try:
                while data := handle.read(64 * 1024):
                    yield data
            finally:
                handle.close()
        disposition = "inline" if preview and metadata["preview_kind"] else "attachment"
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
    except Exception as exc:  # noqa: BLE001 - 统一脱敏响应
        return error_response(exc)
