"""系统模型目录路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response

router = APIRouter()


@router.get("/api/models")
async def list_models(
    request: Request,
    user_id: str,
    tenant_id: str | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.models(user_id, tenant_id))
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)

