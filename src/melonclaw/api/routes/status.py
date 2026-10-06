"""服务状态路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response

router = APIRouter()


@router.get("/api/status")
async def service_status(request: Request) -> JSONResponse:
    return JSONResponse(await get_chat_service(request).status())


@router.get("/api/dev/users")
async def dev_users(request: Request) -> JSONResponse:
    manager = get_chat_service(request)
    if manager.storage is None:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.users())
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/ready")
async def readiness(request: Request) -> JSONResponse:
    """公开探测只返回就绪状态，不暴露配置或初始化错误。"""
    ready = get_chat_service(request).ready
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503,
                        headers={"Cache-Control": "no-store"})
