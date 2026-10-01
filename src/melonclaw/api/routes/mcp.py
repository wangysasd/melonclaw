"""MCP 管理 API；服务层独立校验权限，错误不输出原始配置。"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.mcp_schemas import (
    McpCreateRequest,
    McpDiscoveryRequest,
    McpPreferenceRequest,
    McpStateRequest,
    McpTestRequest,
    McpUpdateRequest,
    McpVersionRequest,
)
from melonclaw.services.mcp import McpConfigError
from melonclaw.services.mcp_management import McpManagementService

router = APIRouter()


def service(request):
    return McpManagementService(get_chat_service(request).runtime)


async def respond(operation, status=200):
    try:
        return JSONResponse(await operation, status_code=status)
    except Exception as exc:
        if isinstance(exc, ValueError) or isinstance(getattr(exc, "status_code", None), int):
            return error_response(exc)
        return JSONResponse({"error": "MCP 操作失败，请检查配置或稍后重试。"}, status_code=500)


@router.get("/api/mcp")
async def list_mcp(request: Request, user_id: str):
    return await respond(service(request).list(user_id))


@router.post("/api/mcp")
async def create_mcp(request: Request, payload: McpCreateRequest):
    return await respond(
        service(request).create(payload.user_id, payload.model_dump(exclude={"user_id"})), 201
    )


@router.put("/api/mcp/preferences/{slug}")
async def preference(request: Request, slug: str, payload: McpPreferenceRequest):
    return await respond(service(request).preference(payload.user_id, slug, payload.enabled))


async def cancellable_test(request, operation):
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if not done and await request.is_disconnected():
                task.cancel()
                raise asyncio.CancelledError
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post("/api/mcp/test")
async def test_draft(request: Request, payload: McpTestRequest):
    if (payload.base_id is None) != (payload.version is None):
        return error_response(McpConfigError("编辑测试需要配置 ID 和版本。"))
    if payload.base_id:
        try:
            UUID(payload.base_id)
        except ValueError:
            return error_response(McpConfigError("配置 ID 无效。"))
    operation = service(request).test(
        payload.user_id,
        payload.model_dump(exclude={"user_id", "base_id", "version"}),
        identifier=payload.base_id,
        version=payload.version,
    )
    return await respond(cancellable_test(request, operation))


@router.get("/api/mcp/{identifier}")
async def detail(request: Request, identifier: UUID, user_id: str):
    return await respond(service(request).detail(user_id, str(identifier)))


@router.patch("/api/mcp/{identifier}")
async def update(request: Request, identifier: UUID, payload: McpUpdateRequest):
    fields = payload.model_dump(exclude_unset=True, exclude={"user_id", "version"})
    # Explicit null only has meaning for nullable connection fields and allowlist.
    if any(
        value is None and key not in {"url", "command", "tool_allowlist"}
        for key, value in fields.items()
    ):
        return error_response(McpConfigError("字段不能为 null。"))
    for key in ("env", "headers"):
        if key in fields:
            fields[key] = getattr(payload, key).model_dump()
    return await respond(
        service(request).update(payload.user_id, str(identifier), payload.version, fields)
    )


@router.delete("/api/mcp/{identifier}")
async def delete(request: Request, identifier: UUID, user_id: str, version: int = Query(ge=1)):
    return await respond(service(request).delete(user_id, str(identifier), version))


@router.put("/api/mcp/{identifier}/global-state")
async def global_state(request: Request, identifier: UUID, payload: McpStateRequest):
    return await respond(
        service(request).global_state(
            payload.user_id, str(identifier), payload.version, payload.enabled
        )
    )


@router.post("/api/mcp/{identifier}/add")
async def add(request: Request, identifier: UUID, payload: McpVersionRequest):
    return await respond(service(request).add(payload.user_id, str(identifier), payload.version))


@router.post("/api/mcp/{identifier}/tools")
async def discover_mcp_tools(request: Request, identifier: UUID, payload: McpDiscoveryRequest):
    return await respond(
        cancellable_test(
            request,
            service(request).discover_tools(
                payload.user_id, str(identifier), payload.version,
                background=payload.background, refresh=payload.refresh,
            ),
        )
    )


@router.post("/api/mcp/{identifier}/test")
async def test_saved(request: Request, identifier: UUID, payload: McpVersionRequest):
    return await respond(
        cancellable_test(
            request,
            service(request).test(
                payload.user_id, identifier=str(identifier), version=payload.version
            ),
        )
    )
