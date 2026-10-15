"""固定任务会话的消息交付、正常聊天隔离与追问。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import update
from test_task_agent import resolved_model, settings_for
from test_task_postgres import claim, database_fixture, enqueue, install_model

from melonclaw.core.agent import AgentContext
from melonclaw.database.schema import users
from melonclaw.middleware.task_context import TaskContextMiddleware
from melonclaw.repository import ConversationBusyError
from melonclaw.repository.errors import ConversationMoveError
from melonclaw.services.conversations import ConversationService
from melonclaw.services.execution import ExecutionService
from melonclaw.services.task_chat import TaskChatService
from melonclaw.services.tasks import TaskService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL"
)


async def history(storage, conversation_id):
    _, rows, _ = await storage.list_messages(
        UUID(str(conversation_id)), "admin", limit=100, before_seq=None
    )
    return rows


async def normal_pair(storage, conversation_id):
    return await storage.create_message_pair(
        UUID(str(conversation_id)),
        "admin",
        None,
        str(uuid4()),
        "继续分析",
        attachment_ids=[],
        model_id="custom:offline",
        model_provider="offline",
        model_name="offline",
        model_display_name="离线",
        model_supports_image=False,
        max_attachment_count=4,
        max_total_bytes=1000,
    )


def test_runs_share_one_chat_and_status_changes_are_delivered(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            saved = await enqueue(service)
            tid, cid = UUID(saved["task"]["id"]), saved["task"]["conversation_id"]
            rows = await history(storage, cid)
            assert len(rows) == 2 and rows[1]["status"] == "pending"
            assert rows[1]["display_metadata"]["task_run"]["status"] == "queued"
            chat = TaskChatService(SimpleNamespace(require_ready=lambda: storage))
            updates = await chat.updates("admin", tid, 2, [UUID(rows[1]["id"])])
            assert len(updates["items"]) == 1 and updates["items"][0]["status"] == "pending"
            owned = await claim(storage.task_queue)
            assert (await history(storage, cid))[1]["display_metadata"]["task_run"][
                "status"
            ] == "running"
            assert await storage.get_incomplete_assistant(UUID(cid), "admin") is None
            assert await storage.get_latest_assistant(UUID(cid), "admin") is None
            assert await storage.converge_stale_pending_messages() == 0
            await storage.task_queue.progress(owned, "部分", max_chars=100)
            assert (await history(storage, cid))[1]["content"] == "部分"
            await storage.task_queue.finish(owned, status="succeeded", result="第一份报告")
            rows = await history(storage, cid)
            assert rows[1]["status"] == "completed" and rows[1]["content"] == "第一份报告"
            delivered = await chat.updates("admin", tid, 2, [UUID(rows[1]["id"])])
            assert delivered["items"][0]["content"] == "第一份报告"
            assert (await chat.updates("admin", tid, 2, []))["items"] == []
            again = await service.run(
                "admin", tid, expected_version=1, idempotency_key=str(uuid4())
            )
            await service.cancel("admin", UUID(again["id"]), idempotency_key=str(uuid4()))
            rows = await history(storage, cid)
            assert len(rows) == 3 and rows[-1]["status"] == "cancelled"
            assert len({row["id"] for row in rows}) == 3
            conversations, _ = await storage.list_conversations("admin", limit=100, cursor=None)
            assert len(conversations) == 1 and conversations[0]["id"] == cid
            assert conversations[0]["task_id"] == str(tid)

    asyncio.run(run())


def test_delivery_transaction_rolls_back_with_run_and_preserves_user_chat(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            saved = await enqueue(service)
            cid, tid = UUID(saved["task"]["conversation_id"]), UUID(saved["task"]["id"])
            owned = await claim(storage.task_queue)
            original = __import__("melonclaw.repository.task_queue", fromlist=["sync_run"]).sync_run

            async def broken(*args):
                raise RuntimeError("delivery failed")

            monkeypatch.setattr("melonclaw.repository.task_queue.sync_run", broken)
            with pytest.raises(RuntimeError):
                await storage.task_queue.finish(owned, status="succeeded", result="不能单独提交")
            assert (await service.get_run("admin", owned["id"]))["status"] == "running"
            assert (await history(storage, cid))[1]["status"] == "pending"
            monkeypatch.setattr("melonclaw.repository.task_queue.sync_run", original)
            await storage.task_queue.finish(owned, status="succeeded", result="完成")
            pair, _ = await asyncio.gather(
                normal_pair(storage, cid),
                service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4())),
            )
            active = await storage.get_incomplete_assistant(cid, "admin")
            assert active["id"] == pair.assistant_message["id"]
            await storage.task_queue.interrupt_running("service_restarted")
            # 排队记录也正常同步，普通聊天占位没有被任务收尾修改。
            queued = (await service.repository.runs("admin", tid))["items"][0]
            await service.cancel("admin", queued["id"], idempotency_key=str(uuid4()))
            rows = await history(storage, cid)
            assert sorted(row["seq"] for row in rows) == list(range(1, 6))
            assert (
                next(row for row in rows if row["id"] == pair.assistant_message["id"])["status"]
                == "pending"
            )

    asyncio.run(run())


def test_followup_binds_accumulated_history_and_retry_keeps_original(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            settings = settings_for(tmp_path)
            service = TaskService(settings, storage)
            saved = await enqueue(service)
            tid, cid = UUID(saved["task"]["id"]), UUID(saved["task"]["conversation_id"])
            await storage.task_queue.finish(
                await claim(storage.task_queue), status="succeeded", result="第一轮报告资料"
            )

            class Agent:
                melonclaw_skill_references = []

                async def aget_state(self, config):
                    return SimpleNamespace(interrupts=(), tasks=(), next=(), values={})

            async def resolve(*args):
                return resolved_model()

            async def skill(*args):
                return None

            async def agent(*args):
                return Agent()

            runtime = SimpleNamespace(
                require_ready=lambda: storage,
                storage=storage,
                resolve_model=resolve,
                resolve_skill=skill,
                agent_for_conversation=agent,
                settings=settings,
                worker_id="web-test",
                workspace_dir=lambda *args: tmp_path,
                conversation_config=lambda id: {"configurable": {"thread_id": str(id)}},
            )
            execution = ExecutionService(runtime, ConversationService(runtime))
            request_id = str(uuid4())
            prepared = await execution.prepare_message(cid, "admin", request_id, "解释这个报告")
            try:
                request = await storage.find_request(cid, "admin", request_id)
                assert (
                    request.user_message["display_metadata"]["task_history"]["completed_runs"]
                    == 1
                )
                provider = TaskChatService(runtime)
                captured = []
                middleware_request = SimpleNamespace(
                    runtime=SimpleNamespace(
                        context=AgentContext(
                            user_id=prepared.user_id,
                            tenant_id=prepared.tenant_id,
                            tenant_name=prepared.tenant_name,
                            conversation_id=str(cid),
                            user_message_id=str(prepared.user_message_id),
                        )
                    ),
                    messages=[
                        HumanMessage(content="解释这个报告", id=str(prepared.user_message_id))
                    ],
                )
                middleware_request.override = lambda **kwargs: SimpleNamespace(**kwargs)

                async def handler(value):
                    captured.extend(value.messages)

                await TaskContextMiddleware(provider).awrap_model_call(middleware_request, handler)
                assert "第一轮报告资料" in str(captured)
                assert "第一轮报告资料" not in middleware_request.messages[0].content
            finally:
                await storage.release_advisory_lock(prepared.lock_connection, cid)
            # 后续任务结果不会改掉已绑定的追问来源，重试沿用本轮固定的累计历史。
            await service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            assert await storage.task_queue.finish(
                await claim(storage.task_queue), status="succeeded", result="第二轮报告资料"
            )
            original = await provider.repository.message_context(
                "admin", cid, prepared.user_message_id
            )
            assert "第一轮报告资料" in str(original["history_messages"])
            assert "第二轮报告资料" not in str(original["history_messages"])

    asyncio.run(run())


def test_task_chat_owner_tenant_delete_and_move_boundaries(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            saved = await enqueue(service)
            tid, cid = UUID(saved["task"]["id"]), UUID(saved["task"]["conversation_id"])
            assert await storage.get_conversation(cid, "other") is None
            with pytest.raises(ConversationBusyError, match="归档"):
                await storage.update_conversation(cid, "admin", delete=True)
            project = await storage.create_project("admin", "项目")
            with pytest.raises(ConversationMoveError, match="任务会话"):
                await storage.move_conversation_to_project(
                    cid,
                    "admin",
                    UUID(project["id"]),
                    attachment_versions=(),
                    workspace_max_bytes=1000,
                )
            await service.archive("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            await storage.update_conversation(cid, "admin", delete=True)
            assert await storage.get_conversation(cid, "admin") is None
            another = await enqueue(service)
            other_cid = UUID(another["task"]["conversation_id"])
            await service.cancel("admin", UUID(another["run"]["id"]), idempotency_key=str(uuid4()))
            ordinary = await storage.create_conversation("admin", None)
            # 模拟身份所属租户改变；历史任务会话不能通过聊天旁路读取。
            async with storage.engine.begin() as conn:
                await conn.execute(
                    update(users).where(users.c.user_id == "admin").values(tenant_id="dep-a")
                )
            assert await storage.get_conversation(other_cid, "admin") is None
            assert await storage.get_conversation(UUID(ordinary["id"]), "admin") is not None

    asyncio.run(run())
