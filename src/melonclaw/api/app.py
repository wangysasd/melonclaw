"""MelonClaw FastAPI 应用组装入口。"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from melonclaw.api.lifespan import lifespan
from melonclaw.api.routes.approvals import router as approvals_router
from melonclaw.api.routes.chat import router as chat_router
from melonclaw.api.routes.conversations import router as conversations_router
from melonclaw.api.routes.projects import router as projects_router
from melonclaw.api.routes.status import router as status_router

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


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""

    application = FastAPI(
        debug=False,
        title="MelonClaw",
        description="Deep Agents 学习项目的浏览器交互 API。",
        lifespan=lifespan,
    )
    # 前后端分离部署时允许跨域调用 /api。
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.include_router(status_router)
    application.include_router(projects_router)
    application.include_router(conversations_router)
    application.include_router(chat_router)
    application.include_router(approvals_router)
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
