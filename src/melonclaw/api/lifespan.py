"""API 应用生命周期管理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from melonclaw.api.background_stream import close_stream_tasks
from melonclaw.repository.task_queue import TaskExecutorUnavailable
from melonclaw.services.chat import ChatService
from melonclaw.services.task_scheduler import TaskScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """启动后台初始化任务，并在应用退出时释放资源。"""

    manager = ChatService()
    application.state.chat = manager
    application.state.task_scheduler = None

    async def initialize():
        await manager.initialize()
        if not manager.ready:
            return
        executor = TaskScheduler(manager.settings, manager.storage, manager.checkpointer, tracing=manager.runtime.tracing)
        application.state.task_scheduler = executor
        try:
            await executor.start()
        except TaskExecutorUnavailable as exc:
            executor.error = str(exc)
            logger.warning("%s", exc)
        except Exception:
            executor.error = "任务调度启动失败，请检查数据库结构和服务配置。"
            logger.warning(executor.error)

    async def expire_environment():
        while True:
            await asyncio.sleep(60)
            if manager.runtime.ready:
                try:
                    await manager.storage.cleanup_environment_operations()
                except Exception:
                    logger.warning("环境变量审批暂存清理未完成，下轮重试。")

    cleanup_task = asyncio.create_task(expire_environment())
    startup_task = asyncio.create_task(initialize())
    application.state.startup_task = startup_task
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        if not startup_task.done():
            startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
        executor = application.state.task_scheduler
        if executor is not None:
            await executor.close()
        await close_stream_tasks()
        await manager.close()
