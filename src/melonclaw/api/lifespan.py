"""API 应用生命周期管理。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from melonclaw.services.chat import ChatService


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """启动后台初始化任务，并在应用退出时释放资源。"""

    manager = ChatService()
    application.state.chat = manager
    startup_task = asyncio.create_task(manager.initialize())
    application.state.startup_task = startup_task
    try:
        yield
    finally:
        if not startup_task.done():
            startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
        await manager.close()
