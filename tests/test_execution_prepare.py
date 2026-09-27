"""消息准备阶段不为忙会话或终态回放构建 Agent。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from melonclaw.repository import ConversationBusyError, RequestRecord
from melonclaw.services.execution import ExecutionService


class _Storage:
    def __init__(self, existing: RequestRecord | None = None, *, busy: bool = False):
        self.existing = existing
        self.busy = busy
        self.calls: list[str] = []

    async def get_conversation(self, conversation_id, user_id):
        return {"id": str(conversation_id), "user_id": user_id, "project_id": None}

    async def find_request(self, conversation_id, user_id, request_id):
        self.calls.append("find_request")
        return self.existing

    async def try_advisory_lock(self, conversation_id):
        self.calls.append("lock")
        return None if self.busy else "connection"

    async def release_advisory_lock(self, connection, conversation_id):
        self.calls.append("unlock")


def _service(storage: _Storage):
    calls: list[str] = []
    model = SimpleNamespace(public_dict=lambda: {"id": "model"})

    async def agent_for_conversation(*args):
        calls.append("agent")
        return object()

    async def async_model(*args, **kwargs):
        return model

    async def resolve_user(user_id):
        return SimpleNamespace(
            user_id=user_id,
            tenant_id="tenant",
            tenant_name_zh="租户",
            tenant_role="owner",
            tenant_status="active",
        )

    async def project_for_conversation(*args):
        return None

    async def resolve_skill(user_id, skill_id):
        return None

    runtime = SimpleNamespace(
        require_ready=lambda: storage,
        resolve_skill=resolve_skill,
        model_for_message=async_model,
        resolve_model=async_model,
        agent_for_conversation=agent_for_conversation,
        workspace_dir=lambda conversation, project: Path("test-workspace"),
        conversation_config=lambda conversation_id: {"configurable": {"thread_id": str(conversation_id)}},
        worker_id="test-worker",
        settings=SimpleNamespace(attachment_max_per_message=3, attachment_max_total_bytes=100),
    )
    conversations = SimpleNamespace(
        resolve_user=resolve_user,
        project_for_conversation=project_for_conversation,
    )
    return ExecutionService(runtime, conversations), calls


def test_busy_message_does_not_build_agent():
    storage = _Storage(busy=True)
    service, calls = _service(storage)

    with pytest.raises(ConversationBusyError):
        asyncio.run(service.prepare_message(uuid4(), "user", "request", "hello"))

    assert storage.calls == ["find_request", "lock"]
    assert calls == []


def test_unavailable_skill_is_rejected_before_any_write():
    """技能选项可能在选中之后被删除（索引行还在、目录没了）。

    此时既不该构建 Agent，也不该落库或抢锁——准备阶段就拦住。
    """

    storage = _Storage()
    service, calls = _service(storage)

    with pytest.raises(ValueError, match="选择的技能不存在或已被移除"):
        asyncio.run(
            service.prepare_message(
                uuid4(), "user", "request", "hello", skill_id="ghost-skill"
            )
        )

    assert storage.calls == []
    assert calls == []


def test_completed_retry_replays_without_lock_or_agent():
    message_id = str(uuid4())
    existing = RequestRecord(
        request_id="request",
        content="hello",
        user_message={"id": str(uuid4()), "display_metadata": {}},
        assistant_message={
            "id": message_id,
            "request_id": "request",
            "status": "completed",
            "content": "answer",
            "assistant_steps": [],
            "display_metadata": {},
        },
    )
    storage = _Storage(existing)
    service, calls = _service(storage)

    prepared = asyncio.run(service.prepare_message(uuid4(), "user", "request", "hello"))

    assert prepared.agent is None
    assert prepared.replay_message is existing.assistant_message
    assert storage.calls == ["find_request"]
    assert calls == []


def test_cancellation_during_agent_build_releases_conversation_lock():
    storage = _Storage()
    service, _ = _service(storage)
    started = asyncio.Event()

    async def slow_build(*args):
        started.set()
        await asyncio.Future()

    service.runtime.agent_for_conversation = slow_build

    async def run():
        task = asyncio.create_task(
            service.prepare_message(uuid4(), "user", "request", "hello")
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert storage.calls[-1] == "unlock"
