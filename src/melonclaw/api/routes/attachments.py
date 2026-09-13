"""附件上传、能力清单、元数据、下载、解析重试和草稿删除 API。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.identity import resolve_request_user_id

router = APIRouter()

# 附件内容是用户上传的不可信数据：禁止浏览器嗅探内容类型，并限制内联执行。
_CONTENT_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=300",
}


@router.get("/api/attachments/capabilities")
async def attachment_capabilities(request: Request) -> JSONResponse:
    """返回支持的类型与限制，作为前端预校验的单一来源。

    必须声明在 ``/api/attachments/{attachment_id}`` 之前，否则会被路径参数吞掉。
    """

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    return JSONResponse(manager.attachment_capabilities())


@router.post("/api/projects/{project_id}/attachments", status_code=201)
async def upload_attachment(
    request: Request,
    project_id: UUID,
    file: UploadFile = File(...),
    user_id: str | None = Form(None),
    tenant_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        resolved_user = resolve_request_user_id(request, user_id)
        result = await manager.attachments.upload(
            project_id,
            resolved_user,
            file,
            client_request_id=client_request_id,
            tenant_id=tenant_id,
        )
        return JSONResponse(result, status_code=201)
    except Exception as exc:  # noqa: BLE001 - 统一返回脱敏错误
        return error_response(exc)


@router.get("/api/attachments/{attachment_id}")
async def attachment_metadata(
    request: Request,
    attachment_id: UUID,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        resolved_user = resolve_request_user_id(request, user_id)
        result = await manager.attachments.metadata(attachment_id, resolved_user, tenant_id)
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回脱敏错误
        return error_response(exc)


@router.get("/api/attachments/{attachment_id}/content", response_model=None)
async def attachment_content(
    request: Request,
    attachment_id: UUID,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> FileResponse | JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        resolved_user = resolve_request_user_id(request, user_id)
        path, metadata = await manager.attachments.content_path(
            attachment_id,
            resolved_user,
            tenant_id,
        )
        return FileResponse(
            path,
            media_type=metadata["media_type"],
            filename=metadata["original_name"],
            headers=_CONTENT_SECURITY_HEADERS,
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回脱敏错误
        return error_response(exc)


@router.post("/api/attachments/{attachment_id}/parse")
async def retry_attachment_parse(
    request: Request,
    attachment_id: UUID,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> JSONResponse:
    """重置解析失败的 staged 附件并重新排队；已在解析中时幂等返回当前状态。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        resolved_user = resolve_request_user_id(request, user_id)
        result = await manager.attachments.retry_parse(
            attachment_id,
            resolved_user,
            tenant_id,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回脱敏错误
        return error_response(exc)


@router.delete("/api/attachments/{attachment_id}")
async def delete_attachment(
    request: Request,
    attachment_id: UUID,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        resolved_user = resolve_request_user_id(request, user_id)
        result = await manager.attachments.delete(attachment_id, resolved_user, tenant_id)
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回脱敏错误
        return error_response(exc)
