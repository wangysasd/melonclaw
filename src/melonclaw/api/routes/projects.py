"""Project 路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import ProjectRequest, ResourceUpdateRequest

router = APIRouter()


@router.patch("/api/projects/{project_id}")
async def update_project(request: Request, project_id: UUID, payload: ResourceUpdateRequest) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        if payload.name is None and payload.is_pinned is None:
            raise ValueError("请提供名称或置顶状态。")
        return JSONResponse(await manager.update_project(
            project_id, payload.user_id, payload.tenant_id,
            name=payload.name, is_pinned=payload.is_pinned,
        ))
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


@router.delete("/api/projects/{project_id}")
async def delete_project(request: Request, project_id: UUID, user_id: str, tenant_id: str | None = None) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        await manager.update_project(project_id, user_id, tenant_id, delete=True)
        return JSONResponse({"deleted": True})
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


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
