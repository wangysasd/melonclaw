"""浏览器断开只解除订阅，业务执行继续写入数据库。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

_tasks: set[asyncio.Task] = set()


async def close_stream_tasks():
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def persistent_stream(events: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
    detached = asyncio.Event()

    async def deliver(item):
        if detached.is_set():
            return
        put = asyncio.create_task(queue.put(item))
        gone = asyncio.create_task(detached.wait())
        try:
            await asyncio.wait({put, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            put.cancel()
            gone.cancel()
            await asyncio.gather(put, gone, return_exceptions=True)

    async def produce():
        try:
            async for event in events:
                await deliver(event)
        finally:
            if asyncio.current_task().cancelling():
                detached.set()
            await deliver(None)
            await events.aclose()

    task = asyncio.create_task(produce())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    try:
        while (item := await queue.get()) is not None:
            yield item
    finally:
        detached.set()
