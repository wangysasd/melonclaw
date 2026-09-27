"""MCP 服务管理路由：列表、新建、启停/白名单、删除。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import McpServerCreateRequest, McpServerUpdateRequest
from melonclaw.services.resource_service import McpServerPayload

router = APIRouter()


@router.get("/api/mcp")
async def list_mcp(request: Request, user_id: str) -> JSONResponse:
    """返回该用户可见可管理的 MCP 服务；env/headers 只回显键名。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.list_mcp(user_id))
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.post("/api/mcp")
async def create_mcp(
    request: Request, payload: McpServerCreateRequest
) -> JSONResponse:
    """新建 MCP 服务；user scope 仅 http/sse 且禁止 ${VAR}，global 需管理员。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.create_mcp(
            payload.user_id,
            McpServerPayload(
                slug=payload.slug,
                scope=payload.scope,
                transport=payload.transport,
                url=payload.url,
                command=payload.command,
                args=list(payload.args),
                env=dict(payload.env),
                headers=dict(payload.headers),
                tool_allowlist=payload.tool_allowlist,
                enabled=payload.enabled,
            ),
        )
        return JSONResponse({"ok": True}, status_code=201)
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.patch("/api/mcp/{slug}")
async def update_mcp(
    request: Request, slug: str, payload: McpServerUpdateRequest
) -> JSONResponse:
    """更新启用状态或工具白名单；权限规则同 Skill。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.update_mcp(
            payload.user_id,
            slug,
            enabled=payload.enabled,
            tool_allowlist=payload.tool_allowlist,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.delete("/api/mcp/{slug}")
async def delete_mcp(request: Request, slug: str, user_id: str) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.delete_mcp(user_id, slug)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)
