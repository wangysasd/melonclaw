"""进程内跨会话 MCP 工具发现缓存。

以已解析的 MCP 配置与白名单 SHA-256 指纹及全局资源版本作为缓存键：
- 凭据、地址、启停或白名单变化自然换键，不会把旧配置或他人凭据混给当前用户；
- 相同共享配置可在不同会话和用户之间复用，避免每个新会话重复连接全部 MCP；
- 仅在内存中保存哈希键与已封装工具对象，不向日志或外部输出原始配置与凭据。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from melonclaw.tool.tools import McpDiscoveryResult, discover_mcp_tools

DEFAULT_SUCCESS_TTL_SECONDS = 600.0
DEFAULT_TRANSIENT_FAILURE_TTL_SECONDS = 30.0
DEFAULT_MAX_ENTRIES = 64

McpDiscoverer = Callable[
    [dict[str, dict[str, Any]], dict[str, tuple[str, ...]] | None],
    Awaitable[McpDiscoveryResult],
]


@dataclass
class _PendingDiscovery:
    task: asyncio.Task[McpDiscoveryResult]
    users: int = 0


def mcp_config_fingerprint(
    servers: dict[str, dict[str, Any]],
    allowlists: dict[str, tuple[str, ...]] | None = None,
) -> str:
    """对已解析的 MCP 连接配置与工具白名单生成稳定 SHA-256 摘要。"""

    payload = {
        "servers": servers,
        "allowlists": {
            name: list(names)
            for name, names in sorted((allowlists or {}).items())
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class McpToolCache:
    """带 TTL、LRU 淘汰和同键单次发现保护的 MCP 工具缓存。"""

    def __init__(
        self,
        *,
        success_ttl_seconds: float = DEFAULT_SUCCESS_TTL_SECONDS,
        transient_failure_ttl_seconds: float = DEFAULT_TRANSIENT_FAILURE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        discover: McpDiscoverer = discover_mcp_tools,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("MCP 工具缓存容量必须为正数。")
        self.success_ttl_seconds = success_ttl_seconds
        self.transient_failure_ttl_seconds = transient_failure_ttl_seconds
        self.max_entries = max_entries
        self._discover = discover
        self._clock = clock
        self._cache: OrderedDict[tuple[str, int], tuple[float, McpDiscoveryResult]] = OrderedDict()
        self._pending: dict[tuple[str, int], _PendingDiscovery] = {}

    async def _run(
        self,
        key: tuple[str, int],
        entry: _PendingDiscovery,
        servers: dict[str, dict[str, Any]],
        allowlists: dict[str, tuple[str, ...]] | None,
    ) -> McpDiscoveryResult:
        try:
            result = await self._discover(servers, allowlists)
            ttl = (
                self.transient_failure_ttl_seconds
                if result.transient_failures
                else self.success_ttl_seconds
            )
            if ttl > 0:
                self._cache[key] = (self._clock() + ttl, result)
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_entries:
                    self._cache.popitem(last=False)
            return result
        finally:
            if self._pending.get(key) is entry:
                self._pending.pop(key, None)

    async def get_or_discover(
        self,
        servers: dict[str, dict[str, Any]],
        allowlists: dict[str, tuple[str, ...]] | None = None,
        *,
        global_version: int = 0,
    ) -> McpDiscoveryResult:
        """命中未过期缓存时直接返回，否则同键合并执行一次 MCP 发现。"""

        if not servers:
            return McpDiscoveryResult()
        key = (mcp_config_fingerprint(servers, allowlists), global_version)
        cached = self._cache.get(key)
        if cached is not None:
            if cached[0] > self._clock():
                self._cache.move_to_end(key)
                return cached[1]
            self._cache.pop(key, None)

        entry = self._pending.get(key)
        if entry is None:
            entry = _PendingDiscovery(task=None)  # type: ignore[arg-type]
            entry.task = asyncio.create_task(self._run(key, entry, servers, allowlists))
            self._pending[key] = entry
        entry.users += 1
        try:
            return await asyncio.shield(entry.task)
        finally:
            entry.users -= 1
            if entry.users == 0:
                if self._pending.get(key) is entry:
                    self._pending.pop(key, None)
                if not entry.task.done():
                    entry.task.cancel()
                await asyncio.gather(entry.task, return_exceptions=True)

    async def close(self) -> None:
        """关闭运行时前取消进行中的发现并清空缓存。"""

        tasks = [entry.task for entry in self._pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._cache.clear()
