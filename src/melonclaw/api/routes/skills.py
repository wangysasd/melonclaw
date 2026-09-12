"""项目 Skill 目录路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response

router = APIRouter()


@router.get("/api/skills")
async def list_skills(request: Request) -> JSONResponse:
    """返回 Skill frontmatter 的安全摘要，不返回正文或宿主路径。"""

    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(manager.skills())
    except Exception as exc:  # noqa: BLE001 - 路由边界统一脱敏
        return error_response(exc)
