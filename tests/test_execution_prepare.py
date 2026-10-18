"""消息准备阶段不为忙会话或终态回放构建 Agent。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from melonclaw.api.schemas import MessageRequest
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

    async def agent_for_conversation(*args, **kwargs):
        calls.append("agent")
        return SimpleNamespace(melonclaw_skill_references=[], melonclaw_resolved_model=await runtime.resolve_model("user"))

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
    """技能从缓存同版本引用验证，错误时不创建消息。"""
    storage = _Storage()
    service, calls = _service(storage)
    with pytest.raises(ValueError, match="选择的技能已不可用"):
        asyncio.run(service.prepare_message(uuid4(), "user", "request", "hello", skill_id="ghost-skill"))
    assert storage.calls[-1] == "unlock"
    assert calls == ["agent"]


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


def test_preparation_reuses_authenticated_user_once_but_reloads_conversation_each_time():
    from melonclaw.services.preparation import prepare_context

    calls = {"user": 0, "conversation": 0}

    class Storage:
        async def get_conversation(self, conversation_id, user_id):
            calls["conversation"] += 1
            return {"id": str(conversation_id), "user_id": user_id, "project_id": None}

    class Conversations:
        runtime = SimpleNamespace(require_ready=lambda: Storage())

        async def resolve_user(self, user_id):
            calls["user"] += 1
            return SimpleNamespace(user_id=user_id, tenant_id="tenant", tenant_status="active")

    conversations = Conversations()
    conversation_id = uuid4()

    async def run():
        first = await prepare_context(conversations, conversation_id, "alice")
        assert first.user.user_id == "alice"
        assert await prepare_context(conversations, conversation_id, "alice", first.user)
        assert calls == {"user": 1, "conversation": 2}

        # A restored request starts a new preparation and resolves its identity again.
        await prepare_context(conversations, conversation_id, "alice")
        assert calls == {"user": 2, "conversation": 3}

    asyncio.run(run())


def test_preparation_rejects_conversation_whose_owner_changed():
    from melonclaw.repository import ConversationNotFoundError
    from melonclaw.services.preparation import prepare_context

    class Storage:
        async def get_conversation(self, conversation_id, user_id):
            return {"id": str(conversation_id), "user_id": "bob", "project_id": None}

    class Conversations:
        runtime = SimpleNamespace(require_ready=lambda: Storage())

        async def resolve_user(self, user_id):
            return SimpleNamespace(user_id=user_id, tenant_id="tenant")

    with pytest.raises(ConversationNotFoundError):
        asyncio.run(prepare_context(Conversations(), uuid4(), "alice"))


def test_cancellation_during_agent_build_releases_conversation_lock():
    storage = _Storage()
    service, _ = _service(storage)
    started = asyncio.Event()

    async def slow_build(*args, **kwargs):
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


def test_message_api_rejects_removed_report_reference():
    with pytest.raises(ValueError):
        MessageRequest.model_validate({"user_id": "admin", "request_id": str(uuid4()),
                                       "content": "追问", "task_run_id": str(uuid4())})


def test_approval_resume_preserves_original_user_message_and_task_context(monkeypatch):
    from uuid import UUID

    from melonclaw.core.hitl import approval_batch_id_for

    user_message_id, assistant_id = uuid4(), uuid4()
    selection = {"task_history": {"task_id": str(uuid4()), "sources": [], "completed_runs": 0}}
    assistant = {"id": str(assistant_id), "request_id": "request", "content": "",
                 "assistant_steps": [], "execution_duration_ms": 0, "display_metadata": {}}
    record = RequestRecord(request_id="request", content="修改任务",
        user_message={"id": str(user_message_id), "display_metadata": selection}, assistant_message=assistant)
    storage = _Storage(record)
    async def incomplete(*args):
        return assistant
    storage.get_incomplete_assistant = incomplete
    service, _ = _service(storage)
    service.runtime.capabilities_for_message = lambda message: []
    pending = [{"id": "interrupt-task", "action_requests": []}]
    async def get_pending(*args):
        return pending
    monkeypatch.setattr("melonclaw.services.execution.aget_pending_approval", get_pending)
    monkeypatch.setattr("melonclaw.services.execution.build_resume_command", lambda *args: "resume")
    prepared, command = asyncio.run(service.prepare_approval(uuid4(), "user", [],
        approval_batch_id=UUID(approval_batch_id_for(pending, assistant_message_id=str(assistant_id))),
        assistant_message_id=assistant_id))
    assert command == "resume" and prepared.resuming
    assert prepared.user_message_id == user_message_id
    assert prepared.task_context == selection


@pytest.mark.parametrize("cancel", [False, True])
def test_task_history_timeout_or_cancel_releases_conversation_lock(monkeypatch, tmp_path, cancel):
    from contextlib import asynccontextmanager

    from test_task_agent import settings_for
    from test_task_reliability import SummaryModel, history_rows, snapshot

    from melonclaw.core.task_errors import TaskError

    storage = _Storage()
    service, calls = _service(storage)
    model = SummaryModel(hang=True)

    async def conversation(conversation_id, user_id):
        return {"id": str(conversation_id), "user_id": user_id, "project_id": None, "task_id": str(uuid4())}

    @asynccontextmanager
    async def connect():
        yield None

    async def capture(*args):
        return snapshot()

    async def load(*args):
        return history_rows()

    async def resolve(*args):
        return SimpleNamespace(context_window=8192)

    storage.get_conversation = conversation
    storage.engine = SimpleNamespace(connect=connect)
    service.runtime.resolve_model = resolve
    service.runtime.settings = settings_for(tmp_path, task_history_timeout_seconds=1, agent_output_reserve=512, agent_summary_keep_tokens=512)
    monkeypatch.setattr("melonclaw.services.task_chat.capture_history", capture)
    monkeypatch.setattr("melonclaw.services.task_chat.load_history", load)
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: model)

    async def run():
        job = asyncio.create_task(service.prepare_message(uuid4(), "user", "request", "追问"))
        await asyncio.wait_for(model.started.wait(), 2)
        if cancel:
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        else:
            with pytest.raises(TaskError) as caught:
                await job
            assert caught.value.error_code == "history_summary_timeout"

    asyncio.run(run())
    assert storage.calls[-1] == "unlock" and calls == ["agent"]
    assert model.stopped
