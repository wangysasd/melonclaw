"""提交/发布协议：取消、失败、作用域与故障关闭。"""

import asyncio

import pytest

from melonclaw.repository.resource_versions import ResourceVersions, resource_change


class Writer:
    def __init__(self):
        self.resource_versions = ResourceVersions()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.committed = False

    @resource_change(user="user_id")
    async def save(self, user_id, fail=False):
        self.entered.set()
        await self.release.wait()
        if fail:
            raise ValueError("rollback")
        self.committed = True


class ScopedWriter:
    def __init__(self):
        self.resource_versions = ResourceVersions()

    @resource_change(scope="scope", owner="owner")
    async def save(self, scope, owner):
        return {"scope": scope, "owner": owner}

    @resource_change(payload="change")
    async def save_payload(self, change):
        return change


def test_cancelled_caller_still_finishes_commit_and_publication():
    async def run():
        writer = Writer()
        job = asyncio.create_task(writer.save("alice"))
        await writer.entered.wait()
        reader = asyncio.create_task(writer.resource_versions.read("alice"))
        await asyncio.sleep(0)
        assert not reader.done()
        job.cancel()
        writer.release.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert writer.committed and await reader == (0, 1)
        assert await writer.resource_versions.read("bob") == (0, 0)

    asyncio.run(run())


def test_rollback_conservatively_invalidates_without_claiming_commit():
    async def run():
        writer = Writer()
        writer.release.set()
        with pytest.raises(ValueError):
            await writer.save("alice", fail=True)
        assert not writer.committed
        assert await writer.resource_versions.read("alice") == (0, 1)

    asyncio.run(run())


def test_publication_failure_blocks_cache_reads(monkeypatch):
    async def run():
        writer = Writer()
        writer.release.set()

        def fail(*args):
            raise RuntimeError("publish failed")

        monkeypatch.setattr(writer.resource_versions, "publish", fail)
        with pytest.raises(RuntimeError):
            await writer.save("alice")
        assert writer.committed
        with pytest.raises(RuntimeError, match="重启"):
            await writer.resource_versions.read("alice")

    asyncio.run(run())


def test_resource_write_publishes_global_and_only_affected_user_versions():
    async def run():
        writer = ScopedWriter()
        await writer.save("global", "admin")
        assert await writer.resource_versions.read("alice") == (1, 0)
        assert await writer.resource_versions.read("bob") == (1, 0)

        await writer.save("user", "alice")
        assert await writer.resource_versions.read("alice") == (1, 1)
        assert await writer.resource_versions.read("bob") == (1, 0)

        await writer.save_payload({"scope": "global", "created_by": "admin"})
        assert await writer.resource_versions.read("alice") == (2, 1)

        await writer.save_payload({"scope": "user", "owner_user_id": "bob", "created_by": "admin"})
        assert await writer.resource_versions.read("alice") == (2, 1)
        assert await writer.resource_versions.read("bob") == (2, 1)

    asyncio.run(run())
