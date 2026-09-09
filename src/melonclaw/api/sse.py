"""Server-Sent Events 编码和流式响应。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

from melonclaw.output.formatting import sanitize_text


def encode_event(event: dict[str, Any], *, event_id: int | None = None) -> bytes:
    """编码一个带序号的 SSE 帧；注释帧由心跳逻辑直接生成。"""

    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return (
        f"{prefix}data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode()


async def stream_events(
    events: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[bytes]:
    """转发 Agent 事件，并在长时间无事件时发送 SSE 心跳。"""

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
                    yield b": keep-alive\n\n"
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                next_event = None
            except asyncio.CancelledError:
                if next_event is not None and not next_event.done():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                raise
            sequence += 1
            yield encode_event(event, event_id=sequence)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 不把后端 traceback 暴露给浏览器
        sequence += 1
        yield encode_event(
            {"type": "error", "message": sanitize_text(str(exc))},
            event_id=sequence,
        )
        sequence += 1
        yield encode_event(
            {"type": "done", "terminal_reason": "transport_error"},
            event_id=sequence,
        )


def stream_response(
    events: AsyncIterator[dict[str, Any]],
) -> StreamingResponse:
    """创建统一配置的 SSE 响应。"""

    return StreamingResponse(
        stream_events(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
