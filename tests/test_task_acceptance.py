"""第五阶段补足进程隔离、管理竞争和当前账户权限的真实 PostgreSQL 验收。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_task_agent import settings_for
from test_task_postgres import claim, database_fixture, enqueue, install_model

from melonclaw.core.task_errors import TaskError
from melonclaw.repository.accounts import AccountConflict
from melonclaw.services.accounts import AccountService
from melonclaw.services.task_chat import TaskChatService
from melonclaw.services.tasks import TaskService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)



def test_pause_and_archive_compete_with_claim_under_real_locks(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            queue = storage.task_queue
            for action in ["pause", "resume", "archive"]:
                saved = await enqueue(service, enabled=action != "resume")
                tid, rid = UUID(saved["task"]["id"]), UUID(saved["run"]["id"])
                change = (
                    service.set_enabled(
                        "admin",
                        tid,
                        enabled=action == "resume",
                        expected_version=1,
                        idempotency_key=str(uuid4()),
                    )
                    if action != "archive"
                    else service.archive(
                        "admin", tid, expected_version=1, idempotency_key=str(uuid4())
                    )
                )
                _, owned = await asyncio.gather(change, claim(queue))
                row = await service.get_run("admin", rid)
                if action != "archive":
                    assert owned and row["status"] == "running"  # 暂停保留已排队记录
                else:
                    assert row["status"] == ("running" if owned else "cancelled")
                    with pytest.raises(TaskError):
                        await service.run(
                            "admin", tid, expected_version=2, idempotency_key=str(uuid4())
                        )
                if owned:
                    assert await queue.finish(owned, status="succeeded", result="已领取记录完成")

    asyncio.run(run())


def test_chat_updates_reject_other_owner_and_disabled_tenant(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            accounts = AccountService(storage)
            await accounts.save_tenant("team", "验收租户", True, create=True)
            await accounts.save_user("alice", "甲", "team", password="test-pass")
            await accounts.save_user("bob", "乙", "team", password="test-pass")
            service = TaskService(settings_for(tmp_path), storage)
            p = TaskChatService(SimpleNamespace(require_ready=lambda: storage))
            saved = await enqueue(service, "alice")
            tid, rid = UUID(saved["task"]["id"]), UUID(saved["run"]["id"])
            for operation in [
                accounts.save_tenant("team", "验收租户", False),
                accounts.delete_user("alice"),
            ]:
                with pytest.raises(AccountConflict):
                    await operation
            queue = storage.task_queue
            await queue.finish(await claim(queue), status="succeeded", result="仅甲可见")
            with pytest.raises(TaskError):
                await p.updates("bob", tid, 0, [])
            await accounts.save_tenant("team", "验收租户", False)
            with pytest.raises(TaskError):
                await p.updates("alice", tid, 0, [])
            assert await service.dispatch_due() == 0
            await accounts.save_tenant("team", "验收租户", True)
            paused = await service.repository.get_task("alice", tid)
            assert paused["status"] == "paused" and "enabled" not in paused["definition"]
            edited = await service.save(
                "alice",
                task_id=tid,
                definition={**paused["definition"], "instruction": "只编辑要求"},
                enabled=False,
                expected_version=paused["version"],
                idempotency_key=str(uuid4()),
            )
            assert edited["task"]["status"] == "paused"
            assert not await queue.progress(
                {"id": rid}, "越权覆盖", max_chars=100
            )

    asyncio.run(run())

