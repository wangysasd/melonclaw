"""服务状态路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import DevUserCreateRequest

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


@router.post("/api/dev/users")
async def create_dev_user(request: Request, body: DevUserCreateRequest) -> JSONResponse:
    manager = get_chat_service(request)
    if manager.storage is None:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.create_user(body.actor_user_id, body.user_id, body.user_name_zh)
        return JSONResponse({"ok": True}, status_code=201)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)
