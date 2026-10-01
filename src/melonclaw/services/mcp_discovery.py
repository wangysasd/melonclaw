"""进程内 MCP 发现调度：手动优先、连接限流、目录缓存与共享取消。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic


class McpDiscoveryBusyError(Exception):
    """排队期限已到，尚未进行连接。"""


@dataclass
class _Discovery:
    task: asyncio.Task = field(init=False)
    background: bool = False
    users: int = 0
    ticket: list[int] | None = None


class McpDiscoveryCoordinator:
    """只缓存脱敏结果；调用者必须先完成归属和版本校验。"""

    def __init__(self, *, concurrency=4, queue_timeout=5, cache_ttl=30, cache_size=128):
        self.concurrency = concurrency
        self.queue_timeout = queue_timeout
        self.cache_ttl = cache_ttl
        self.cache_size = cache_size
        self._condition = asyncio.Condition()
        self._active = 0
        self._sequence = 0
        self._queue = []
        self._pending = {}
        self._cache = OrderedDict()

    @asynccontextmanager
    async def _slot(self, entry):
        acquired = False
        async with self._condition:
            self._sequence += 1
            ticket = [int(entry.background), self._sequence]
            entry.ticket = ticket
            self._queue.append(ticket)
            try:
                async with asyncio.timeout(self.queue_timeout):
                    await self._condition.wait_for(
                        lambda: self._active < self.concurrency and min(self._queue) is ticket
                    )
                self._active += 1
                acquired = True
            except TimeoutError:
                raise McpDiscoveryBusyError from None
            finally:
                self._queue.remove(ticket)
                entry.ticket = None
                self._condition.notify_all()
        try:
            yield
        finally:
            if acquired:
                async with self._condition:
                    self._active -= 1
                    self._condition.notify_all()

    async def _run(self, entry, probe, key):
        try:
            async with self._slot(entry):
                result = await probe()
        except McpDiscoveryBusyError:
            result = {
                "ok": False,
                "error_code": "busy",
                "tool_count": 0,
                "tool_names": [],
                "tool_details": [],
                "message": "检测繁忙，请稍后重试。",
            }
        if key is not None and result["ok"]:
            self._cache[key] = (monotonic() + self.cache_ttl, deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return result

    async def discover(self, probe, *, key=None, background=False, refresh=False):
        if key is not None and not refresh:
            cached = self._cache.get(key)
            if cached and cached[0] > monotonic():
                return deepcopy(cached[1])
            self._cache.pop(key, None)
        if key is not None and refresh:
            self._cache.pop(key, None)
        pending_key = key if key is not None else object()
        entry = self._pending.get(pending_key)
        if entry is None:
            # The task starts after this synchronous section publishes the entry.
            entry = _Discovery(background=background)
            entry.task = asyncio.create_task(self._run(entry, probe, key))
            self._pending[pending_key] = entry
        entry.users += 1
        try:
            if not background:
                entry.background = False
                async with self._condition:
                    if entry.ticket is not None:
                        entry.ticket[0] = 0
                    self._condition.notify_all()
            return deepcopy(await asyncio.shield(entry.task))
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._pending.pop(pending_key, None)
                if not entry.task.done():
                    entry.task.cancel()
                await asyncio.gather(entry.task, return_exceptions=True)

    async def close(self):
        tasks = [entry.task for entry in self._pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._cache.clear()
