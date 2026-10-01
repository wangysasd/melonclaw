"""发现协调器的队列、缓存与取消语义；不依赖外部服务。"""

import asyncio

from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator

RESULT = {"ok": True, "tools": [{"name": "search"}]}


def test_coalesces_requests_and_last_cancel_releases_connection():
    async def run():
        coordinator = McpDiscoveryCoordinator(concurrency=1)
        started, released, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def probe():
            nonlocal calls
            calls += 1
            started.set()
            try:
                await released.wait()
                return RESULT
            finally:
                stopped.set()

        first = asyncio.create_task(coordinator.discover(probe, key=("a", 1)))
        await started.wait()
        second = asyncio.create_task(coordinator.discover(probe, key=("a", 1)))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert not stopped.is_set() and calls == 1
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        assert stopped.is_set() and coordinator._active == 0
        assert not coordinator._pending and not coordinator._queue
        await coordinator.close()

    asyncio.run(run())


def test_cache_version_ttl_refresh_and_copy(monkeypatch):
    from melonclaw.services import mcp_discovery

    now = [0]
    monkeypatch.setattr(mcp_discovery, "monotonic", lambda: now[0])

    async def run():
        coordinator = McpDiscoveryCoordinator(cache_ttl=30, cache_size=2)
        calls = 0

        async def probe():
            nonlocal calls
            calls += 1
            return RESULT

        first = await coordinator.discover(probe, key=("a", 1))
        first["tools"].clear()
        assert (await coordinator.discover(probe, key=("a", 1)))["tools"]
        assert calls == 1
        await coordinator.discover(probe, key=("a", 1), refresh=True)
        await coordinator.discover(probe, key=("a", 2))
        assert calls == 3
        now[0] = 31
        await coordinator.discover(probe, key=("a", 1))
        await coordinator.discover(probe)  # Drafts are never cached or coalesced.
        await coordinator.discover(probe)
        assert calls == 6
        await coordinator.discover(probe, key=("b", 1))
        assert len(coordinator._cache) == 2
        await coordinator.close()
        assert not coordinator._cache

    asyncio.run(run())


def test_busy_queue_does_not_report_connection_timeout():
    async def run():
        coordinator = McpDiscoveryCoordinator(concurrency=1, queue_timeout=0.01)
        started, released = asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await released.wait()
            return RESULT

        active = asyncio.create_task(coordinator.discover(slow))
        await started.wait()
        queued = await coordinator.discover(slow)
        assert not queued["ok"] and queued["error_code"] == "busy"
        assert "繁忙" in queued["message"]
        released.set()
        assert (await active)["ok"]
        assert not coordinator._queue
        await coordinator.close()

    asyncio.run(run())


def test_manual_requests_precede_queued_background_checks():
    async def run():
        coordinator = McpDiscoveryCoordinator(concurrency=1)
        started, released = asyncio.Event(), asyncio.Event()
        order = []

        async def block():
            started.set()
            await released.wait()
            return RESULT

        def probe(name):
            async def discover():
                order.append(name)
                return RESULT

            return discover

        active = asyncio.create_task(coordinator.discover(block))
        await started.wait()
        background = asyncio.create_task(coordinator.discover(probe("background"), background=True))
        manual = asyncio.create_task(coordinator.discover(probe("manual")))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        released.set()
        await asyncio.gather(active, background, manual)
        assert order == ["manual", "background"]
        await coordinator.close()

    asyncio.run(run())
