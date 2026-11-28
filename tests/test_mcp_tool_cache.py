"""跨会话 MCP 工具发现缓存的命中、失效、TTL、并发合并与取消语义测试。"""

from __future__ import annotations

import asyncio

import pytest

from melonclaw.services.mcp_tool_cache import McpToolCache
from melonclaw.tool.tools import McpDiscoveryResult


def _server(url: str = "https://mcp.test/rpc", token: str = "tok-a") -> dict[str, dict[str, object]]:
    return {
        "demo": {
            "transport": "http",
            "url": url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    }


def test_cache_reuses_identical_config_and_invalidates_on_config_or_global_version():
    calls = 0

    async def discover(servers, allowlists):
        nonlocal calls
        calls += 1
        return McpDiscoveryResult(catalog={"demo": (f"tool_{calls}",)})

    now = [100.0]
    cache = McpToolCache(discover=discover, clock=lambda: now[0])

    async def run():
        first = await cache.get_or_discover(_server(), {"demo": ("a",)}, global_version=1)
        second = await cache.get_or_discover(_server(), {"demo": ("a",)}, global_version=1)
        assert first is second
        assert calls == 1

        # 变更凭据 -> 新指纹 -> 重新发现
        changed_cred = await cache.get_or_discover(
            _server(token="tok-b"), {"demo": ("a",)}, global_version=1,
        )
        assert changed_cred is not first
        assert calls == 2

        # 变更白名单 -> 新指纹 -> 重新发现
        changed_allowlist = await cache.get_or_discover(
            _server(), {"demo": ("a", "b")}, global_version=1,
        )
        assert changed_allowlist is not first
        assert calls == 3

        # 变更全局资源版本 -> 重新发现
        bumped_global = await cache.get_or_discover(
            _server(), {"demo": ("a",)}, global_version=2,
        )
        assert bumped_global is not first
        assert calls == 4

        # 空服务列表不触发发现也不写入缓存
        empty = await cache.get_or_discover({}, {}, global_version=2)
        assert empty == McpDiscoveryResult()
        assert calls == 4

    asyncio.run(run())


def test_ttl_separates_success_and_transient_failures_and_deterministic_uses_success_ttl():
    results = [
        McpDiscoveryResult(
            failures={"cfg": "凭据或配置无法解析，请检查配置。"},
            transient_failures=frozenset(),
        ),
        McpDiscoveryResult(
            failures={"net": "连接超时"},
            transient_failures=frozenset({"net"}),
        ),
        McpDiscoveryResult(catalog={"demo": ("recovered",)}),
    ]
    calls = 0

    async def discover(servers, allowlists):
        nonlocal calls
        result = results[calls]
        calls += 1
        return result

    now = [0.0]
    cache = McpToolCache(
        success_ttl_seconds=600.0,
        transient_failure_ttl_seconds=30.0,
        discover=discover,
        clock=lambda: now[0],
    )

    async def run():
        # 确定性配置错误走 600s 成功 TTL
        r1 = await cache.get_or_discover(_server("https://a.test"), global_version=1)
        now[0] = 599.0
        assert await cache.get_or_discover(_server("https://a.test"), global_version=1) is r1
        assert calls == 1

        # 超过 600s 后重新发现，拿到临时连接失败（30s TTL）
        now[0] = 601.0
        r2 = await cache.get_or_discover(_server("https://a.test"), global_version=1)
        assert r2.transient_failures == frozenset({"net"})
        assert calls == 2

        now[0] = 630.0
        assert await cache.get_or_discover(_server("https://a.test"), global_version=1) is r2
        assert calls == 2

        # 超过 30s 后重新发现，恢复成功
        now[0] = 632.0
        r3 = await cache.get_or_discover(_server("https://a.test"), global_version=1)
        assert not r3.transient_failures
        assert r3.catalog == {"demo": ("recovered",)}
        assert calls == 3

    asyncio.run(run())


def test_lru_eviction_and_close():
    calls = 0

    async def discover(servers, allowlists):
        nonlocal calls
        calls += 1
        return McpDiscoveryResult(catalog={"demo": (f"t{calls}",)})

    cache = McpToolCache(max_entries=2, discover=discover)

    async def run():
        a = await cache.get_or_discover(_server("https://1.test"))
        await cache.get_or_discover(_server("https://2.test"))
        # 再次访问 1，让 2 成为最久未使用
        assert await cache.get_or_discover(_server("https://1.test")) is a
        await cache.get_or_discover(_server("https://3.test"))
        assert calls == 3
        assert await cache.get_or_discover(_server("https://1.test")) is a
        assert calls == 3
        # 2 已被淘汰，重新发现
        await cache.get_or_discover(_server("https://2.test"))
        assert calls == 4

        await cache.close()
        await cache.get_or_discover(_server("https://2.test"))
        assert calls == 5

    asyncio.run(run())


def test_concurrent_single_flight_and_waiter_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    cancelled_inside = False

    async def discover(servers, allowlists):
        nonlocal calls, cancelled_inside
        calls += 1
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_inside = True
            raise
        return McpDiscoveryResult(catalog={"demo": ("ok",)})

    cache = McpToolCache(discover=discover)

    async def run():
        # 两个等待者共享一次发现；取消其中一个不影响另一个
        first = asyncio.create_task(cache.get_or_discover(_server("https://shared.test")))
        await started.wait()
        second = asyncio.create_task(cache.get_or_discover(_server("https://shared.test")))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not cancelled_inside
        release.set()
        result = await second
        assert result.catalog == {"demo": ("ok",)}
        assert calls == 1

        # 唯一等待者取消时，底层发现也被取消且不留悬挂项
        started.clear()
        release.clear()
        solo = asyncio.create_task(cache.get_or_discover(_server("https://solo.test")))
        await started.wait()
        solo.cancel()
        with pytest.raises(asyncio.CancelledError):
            await solo
        assert cancelled_inside
        assert cache._pending == {}

    asyncio.run(run())


def test_exception_is_not_cached_and_next_call_retries():
    calls = 0

    async def discover(servers, allowlists):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected failure")
        return McpDiscoveryResult(catalog={"demo": ("ok",)})

    cache = McpToolCache(discover=discover)

    async def run():
        with pytest.raises(RuntimeError, match="unexpected failure"):
            await cache.get_or_discover(_server())
        assert cache._pending == {}
        assert cache._cache == {}
        result = await cache.get_or_discover(_server())
        assert result.catalog == {"demo": ("ok",)}
        assert calls == 2

    asyncio.run(run())
