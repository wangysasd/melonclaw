"""Skill 目录与管理路由：列表、导入、下载、启停和删除。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import (
    SkillGlobalStateRequest,
    SkillImportConfirmRequest,
    SkillRemoteInstallRequest,
    SkillUpdateRequest,
)

router = APIRouter()


@router.get("/api/skills")
async def list_skills(request: Request, user_id: str) -> JSONResponse:
    """返回该用户可见 Skill frontmatter 的安全摘要，不含正文或宿主路径。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.skills(user_id))
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.get("/api/skills/manage")
async def list_manageable_skills(request: Request, user_id: str) -> JSONResponse:
    """返回资源管理页可展示的 Skill；停用的共享项只对管理员可见。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.manageable_skills(user_id))
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.post("/api/skills/recover")
async def recover_skills(request: Request, user_id: str) -> JSONResponse:
    try:
        return JSONResponse(await get_chat_service(request).recover_skills(user_id))
    except Exception as exc:
        return error_response(exc)


@router.get("/api/skills/{name}/details")
async def skill_details(request: Request, name: str, user_id: str, scope: Literal["global", "user"]) -> JSONResponse:
    try:
        return JSONResponse(await get_chat_service(request).skill_details(user_id, name, scope))
    except Exception as exc:
        return error_response(exc)


@router.post("/api/skills/import/prepare")
async def prepare_skill_import(
    request: Request,
    user_id: str = Form(...),
    file: UploadFile = File(...),
    target_id: UUID | None = Form(None),
) -> JSONResponse:
    """上传 ZIP 生成待确认草稿；校验失败直接 422，不产生草稿。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        archive = await file.read(50 * 1024 * 1024 + 1)
        if len(archive) > 50 * 1024 * 1024:
            return JSONResponse({"detail": "上传 ZIP 超过 50MB 上限。"}, status_code=413)
        return JSONResponse(await manager.prepare_skill_import(user_id, archive, str(target_id) if target_id else None))
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.post("/api/skills/install/remote")
async def prepare_remote_install(
    request: Request, payload: SkillRemoteInstallRequest
) -> JSONResponse:
    """从 GitHub 下载 Skill 包并生成待确认草稿（两段式确认与 ZIP 一致）。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.prepare_remote_skill_install(payload.user_id, payload.repo, str(payload.target_id) if payload.target_id else None)
        )
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.post("/api/skills/import/confirm")
async def confirm_skill_import(
    request: Request, payload: SkillImportConfirmRequest
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.confirm_skill_import(payload.user_id, payload.draft_id)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.post("/api/skills/import/cancel")
async def cancel_skill_import(
    request: Request, payload: SkillImportConfirmRequest
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.cancel_skill_import(payload.user_id, payload.draft_id)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.patch("/api/skills/{name}")
async def update_skill(
    request: Request, name: str, payload: SkillUpdateRequest
) -> JSONResponse:
    """个人启用/停用：共享 Skill 写个人偏好（只影响自己），私有仅限创建者。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.set_skill_enabled(
            payload.user_id, name, payload.enabled, payload.scope
        )
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.get("/api/skills/{name}/download")
async def download_skill(
    request: Request, name: str, user_id: str, scope: Literal["global", "user"]
) -> Response:
    """下载当前用户可见 Skill 的 ZIP 包。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        archive = await manager.download_skill_archive(user_id, name, scope)
        return Response(
            content=archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
        )
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.patch("/api/skills/{name}/global-state")
async def update_skill_global_state(
    request: Request, name: str, payload: SkillGlobalStateRequest
) -> JSONResponse:
    """全员启用/停用共享 Skill；仅 admin/owner，对所有用户生效。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.set_skill_global_enabled(payload.user_id, name, payload.enabled)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)


@router.delete("/api/skills/{name}")
async def delete_skill(
    request: Request, name: str, user_id: str, scope: Literal["global", "user"]
) -> JSONResponse:
    """删除 Skill（数据库行 + 内容目录）；权限同 update。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.delete_skill(user_id, name, scope)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)
