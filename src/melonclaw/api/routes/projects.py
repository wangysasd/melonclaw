"""Project 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import ProjectRequest

router = APIRouter()


@router.post("/api/projects", status_code=201)
async def create_project(
    request: Request,
    payload: ProjectRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.create_project(
                payload.user_id,
                payload.name,
                payload.tenant_id,
            ),
            status_code=201,
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/projects")
async def list_projects(
    request: Request,
    user_id: str,
    tenant_id: str | None = None,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(
            {"items": await manager.list_projects(user_id, tenant_id)}
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)
