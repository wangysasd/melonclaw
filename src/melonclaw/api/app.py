"""MelonClaw FastAPI 应用组装入口。"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from melonclaw.api.identity import identity_header_name
from melonclaw.api.lifespan import lifespan
from melonclaw.api.routes.approvals import router as approvals_router
from melonclaw.api.routes.attachments import router as attachments_router
from melonclaw.api.routes.chat import router as chat_router
from melonclaw.api.routes.conversations import router as conversations_router
from melonclaw.api.routes.mcp import router as mcp_router
from melonclaw.api.routes.models import router as models_router
from melonclaw.api.routes.projects import router as projects_router
from melonclaw.api.routes.results import router as results_router
from melonclaw.api.routes.skills import router as skills_router
from melonclaw.api.routes.status import router as status_router
from melonclaw.api.routes.user_input import router as user_input_router

DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:8001",
    "http://127.0.0.1:8001",
)


def allowed_origins() -> list[str]:
    """解析前端独立部署时的允许来源。"""

    raw = os.getenv("MELONCLAW_ALLOWED_ORIGINS", "")
    origins = [
        origin.strip().rstrip("/")
        for origin in raw.split(",")
        if origin.strip()
    ]
    if origins:
        return origins
    return list(DEFAULT_ALLOWED_ORIGINS)


def allowed_headers() -> list[str]:
    """跨域前端需要显式放行自定义身份请求头（仅在部署方配置时）。"""

    headers = ["Content-Type"]
    identity_header = identity_header_name()
    if identity_header:
        headers.append(identity_header)
    return headers


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""

    application = FastAPI(
        debug=False,
        title="MelonClaw",
        description="Deep Agents 学习项目的浏览器交互 API。",
        lifespan=lifespan,
    )
    @application.exception_handler(RequestValidationError)
    async def safe_validation(request, exc):
        if request.url.path.startswith("/api/mcp"):
            return JSONResponse({"error": "MCP 字段格式无效，请检查名称、标识和连接配置。"}, status_code=422)
        if request.url.path.endswith("/messages"):
            return JSONResponse({"error": "消息字段格式无效，正文最多 12000 字符；请检查请求。"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    # 前后端分离部署时允许跨域调用 /api。
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=allowed_headers(),
    )
    application.include_router(status_router)
    application.include_router(models_router)
    application.include_router(skills_router)
    application.include_router(mcp_router)
    application.include_router(projects_router)
    application.include_router(attachments_router)
    application.include_router(results_router)
    application.include_router(conversations_router)
    application.include_router(chat_router)
    application.include_router(approvals_router)
    application.include_router(user_input_router)
    return application


app = create_app()


def main() -> None:
    """启动本地 Web 服务。"""

    import uvicorn

    host = os.getenv("MELONCLAW_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("MELONCLAW_PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("MELONCLAW_PORT 必须是整数。") from exc
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
