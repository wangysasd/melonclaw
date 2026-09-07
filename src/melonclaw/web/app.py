"""MelonClaw FastAPI Web 应用与 HTTP/SSE 接口。"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from melonclaw.core.database import (
    ConversationBusyError,
    ConversationNotFoundError,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
    ProjectNotFoundError,
    RequestConflictError,
)
from melonclaw.output.streaming import sanitize_text
from melonclaw.web.service import (
    ChatService,
    InvalidUserError,
    RequestInProgressError,
)

WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
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


app = FastAPI(
    debug=False,
    title="MelonClaw",
    description="Deep Agents 学习项目的浏览器交互 API。",
    lifespan=lifespan,
)


def _manager(request: Request) -> ChatService:
    return request.app.state.chat


@app.get("/", include_in_schema=False)
async def homepage() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/status")
async def service_status(request: Request) -> JSONResponse:
    return JSONResponse(_manager(request).status())


@app.get("/api/dev/users")
async def dev_users(request: Request) -> JSONResponse:
    manager = _manager(request)
    if manager.storage is None:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.users())
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return _error_response(exc)


class ConversationRequest(BaseModel):
    """创建会话时的开发用户和当前租户运行上下文；会话本身不绑定租户。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: UUID | None = None


class ProjectRequest(BaseModel):
    """创建 Project 时的开发用户、租户和名称。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class MessageRequest(BaseModel):
    """浏览器发送给 Agent 的一轮消息和可选用户标签。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    request_id: UUID
    content: str = Field(min_length=1, max_length=12000)


class ApprovalRequest(BaseModel):
    """浏览器提交的 HITL 审批决定和可选用户标签。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    decisions: list[dict[str, Any]]


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, (ConversationNotFoundError, ProjectNotFoundError)):
        status_code = 404
    elif isinstance(exc, InvalidUserError):
        status_code = 400
    elif isinstance(
        exc,
        (
            ConversationBusyError,
            RequestConflictError,
            RequestInProgressError,
        ),
    ):
        status_code = 409
    elif isinstance(
        exc,
        (DatabaseConfigurationError, DatabaseSchemaError, DatabaseUnavailableError),
    ):
        status_code = 503
    elif isinstance(exc, ValueError):
        status_code = 400
    else:
        status_code = 500
    return JSONResponse(
        {"error": sanitize_text(str(exc)) or "请求处理失败。"},
        status_code=status_code,
    )


def _sse(event: dict[str, Any], *, event_id: int | None = None) -> bytes:
    """编码一个带序号的 SSE 帧；注释帧由心跳逻辑直接生成。"""

    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return (
        f"{prefix}data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode()


async def _stream_events(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    iterator = events.__aiter__()
    sequence = 0
    next_event: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if next_event is None:
                next_event = asyncio.create_task(iterator.__anext__())
            try:
                done, _ = await asyncio.wait({next_event}, timeout=15.0)
                if not done:
                    # SSE 注释不会触发浏览器的 message 事件，但能避免代理或
                    # 负载均衡器把长时间运行的 Agent 流判定为空闲连接。
                    yield b": keep-alive\n\n"
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                next_event = None
            except asyncio.CancelledError:
                if next_event is not None and not next_event.done():
                    # 客户端断开时，避免遗留一个仍持有 Agent/数据库锁的任务。
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                raise
            sequence += 1
            yield _sse(event, event_id=sequence)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 不把后端 traceback 暴露给浏览器
        sequence += 1
        yield _sse(
            {"type": "error", "message": sanitize_text(str(exc))},
            event_id=sequence,
        )
        sequence += 1
        yield _sse(
            {"type": "done", "terminal_reason": "transport_error"},
            event_id=sequence,
        )


def _stream_response(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(
        _stream_events(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/conversations", status_code=201)
async def create_conversation(
    request: Request,
    payload: ConversationRequest,
) -> JSONResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.create_conversation(
                payload.user_id,
                payload.project_id,
                payload.tenant_id,
            ),
            status_code=201,
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return _error_response(exc)


@app.post("/api/projects", status_code=201)
async def create_project(
    request: Request,
    payload: ProjectRequest,
) -> JSONResponse:
    manager = _manager(request)
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
        return _error_response(exc)


@app.get("/api/projects")
async def list_projects(
    request: Request,
    user_id: str,
    tenant_id: str | None = None,
) -> JSONResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        return JSONResponse(
            {"items": await manager.list_projects(user_id, tenant_id)}
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return _error_response(exc)


@app.get("/api/conversations")
async def list_conversations(
    request: Request,
    user_id: str,
    tenant_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    project_id: UUID | None = None,
) -> JSONResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        items, next_cursor = await manager.list_conversations(
            user_id,
            tenant_id=tenant_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
        )
        return JSONResponse({"items": items, "next_cursor": next_cursor})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return _error_response(exc)


@app.get("/api/conversations/{conversation_id}/messages")
async def conversation_history(
    request: Request,
    conversation_id: UUID,
    user_id: str,
    tenant_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    before_seq: int | None = Query(default=None, ge=1),
) -> JSONResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        result = await manager.history(
            conversation_id,
            user_id,
            tenant_id=tenant_id,
            limit=limit,
            before_seq=before_seq,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return _error_response(exc)


@app.post(
    "/api/conversations/{conversation_id}/messages",
    response_model=None,
    response_class=StreamingResponse,
)
async def send_message(
    request: Request,
    conversation_id: UUID,
    payload: MessageRequest,
) -> JSONResponse | StreamingResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        execution = await manager.prepare_message(
            conversation_id,
            payload.user_id,
            str(payload.request_id),
            payload.content,
            payload.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - 准备阶段需要真实 HTTP 状态码
        return _error_response(exc)
    return _stream_response(manager.stream_execution(execution))


@app.post(
    "/api/conversations/{conversation_id}/approval",
    response_model=None,
    response_class=StreamingResponse,
)
async def submit_approval(
    request: Request,
    conversation_id: UUID,
    payload: ApprovalRequest,
) -> JSONResponse | StreamingResponse:
    manager = _manager(request)
    if not manager.ready:
        return JSONResponse(manager.status(), status_code=503)
    try:
        execution, command = await manager.prepare_approval(
            conversation_id,
            payload.user_id,
            payload.decisions,
            payload.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - 准备阶段需要真实 HTTP 状态码
        return _error_response(exc)
    return _stream_response(manager.stream_execution(execution, agent_input=command))


app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


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
