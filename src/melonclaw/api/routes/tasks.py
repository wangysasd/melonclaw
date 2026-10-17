"""任务管理仅使用 Cookie 当前身份；不接受客户端租户或 owner。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from melonclaw.api.dependencies import get_chat_service
from melonclaw.core.task_snapshot import TaskDefinition
from melonclaw.repository.tasks import json_value
from melonclaw.services.tasks import TaskService

router = APIRouter()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


class TaskAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_version: int = Field(ge=1)


class TaskSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: TaskDefinition
    enabled: bool
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_version: int | None = Field(default=None, ge=1)
    run_now: bool = False


def service(request):
    manager = get_chat_service(request)
    if not manager.ready:
        raise HTTPException(503, "服务尚未就绪，请检查数据库升级。")
    return TaskService(manager.settings, manager.storage)


@router.get("/api/tasks/status")
async def status(request: Request):
    current = service(request)
    executor = getattr(request.app.state, "task_scheduler", None)
    return json_value(
        {
            "executor": executor.status() if executor else {
                "ready": False, "running": 0, "last_scan_at": None, "error": None,
            },
            "schedule_kinds": ["manual", "once", "daily", "weekly"],
            "timezone": "Asia/Shanghai",
            "search_available": bool(current.settings.deepseek_api_key.strip()),
        }
    )


@router.get("/api/tasks")
async def list_tasks(
    request: Request,
    name: str = Query("", max_length=120),
    status: Literal["enabled", "paused", "archived"] | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    return json_value(
        await service(request).repository.list_tasks(
            request.state.user.user_id,
            name=name,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/api/tasks", status_code=201)
async def create_task(request: Request, payload: TaskSave):
    return await service(request).save(request.state.user.user_id, **payload.model_dump())


@router.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: UUID):
    return json_value(
        await service(request).repository.get_task(request.state.user.user_id, task_id)
    )


@router.patch("/api/tasks/{task_id}")
async def update_task(request: Request, task_id: UUID, payload: TaskSave):
    return await service(request).save(
        request.state.user.user_id, task_id=task_id, **payload.model_dump()
    )


@router.post("/api/tasks/{task_id}/runs", status_code=202)
async def run_task(request: Request, task_id: UUID, payload: TaskAction):
    return await service(request).run(request.state.user.user_id, task_id, **payload.model_dump())


@router.post("/api/tasks/{task_id}/archive")
async def archive_task(request: Request, task_id: UUID, payload: TaskAction):
    return await service(request).archive(
        request.state.user.user_id, task_id, **payload.model_dump()
    )


@router.get("/api/tasks/{task_id}/messages")
async def task_message_updates(request: Request, task_id: UUID,
    after_seq: int = Query(0, ge=0), pending_message_ids: list[UUID] = Query(default=[])):
    from melonclaw.services.task_chat import TaskChatService

    if len(pending_message_ids) > 100:
        raise HTTPException(422, "同步消息数量超过限制。")
    service(request)
    return await TaskChatService(get_chat_service(request).runtime).updates(
        request.state.user.user_id, task_id, after_seq, pending_message_ids,
    )


@router.get("/api/tasks/{task_id}/runs")
async def task_history(request: Request, task_id: UUID, page: Page = 1, page_size: PageSize = 20):
    return json_value(
        await service(request).repository.runs(
            request.state.user.user_id,
            task_id,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/api/task-runs/{run_id}")
async def get_run(request: Request, run_id: UUID):
    return await service(request).get_run(request.state.user.user_id, run_id)


class TaskEnabled(TaskAction):
    enabled: bool


class TaskCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


@router.post("/api/tasks/{task_id}/enabled")
async def set_enabled(request: Request, task_id: UUID, payload: TaskEnabled):
    return await service(request).set_enabled(
        request.state.user.user_id, task_id, **payload.model_dump()
    )


@router.post("/api/task-runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: UUID, payload: TaskCancel):
    return await service(request).cancel(request.state.user.user_id, run_id, **payload.model_dump())



