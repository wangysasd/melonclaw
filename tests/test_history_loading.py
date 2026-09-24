"""历史查询在普通终态跳过 Checkpoint，待恢复轮次仍沿用原能力。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from melonclaw.services import conversations as conversation_module
from melonclaw.services.conversations import ConversationService


class _Storage:
    def __init__(self, latest_status: str):
        self.conversation_id = uuid4()
        self.assistant_id = uuid4()
        self.latest_status = latest_status
        self.calls: list[str] = []

    async def list_messages(self, conversation_id, user_id, *, limit, before_seq):
        self.calls.append("list_messages")
        return (
            {"id": str(conversation_id), "user_id": user_id, "project_id": None},
            [{"id": str(self.assistant_id), "role": "assistant", "status": self.latest_status}],
            None,
        )

    async def list_attachments_for_messages(self, message_ids):
        return {}

    async def get_incomplete_assistant(self, conversation_id, user_id):
        return None

    async def get_recovery_required_interaction(self, conversation_id, user_id):
        return None

    async def get_latest_assistant(self, conversation_id, user_id):
        return {
            "id": str(self.assistant_id),
            "status": self.latest_status,
            "request_id": "request",
            "display_metadata": {},
        }

    async def get_request_user_message(self, conversation_id, user_id, request_id):
        self.calls.append("get_request_user_message")
        return {"display_metadata": {"capabilities": ["ask_user"]}}


def _service(storage: _Storage):
    agent_calls: list[tuple[str, ...]] = []

    async def agent_for_conversation(conversation, project, model, capabilities):
        agent_calls.append(capabilities)
        return object()

    runtime = SimpleNamespace(
        require_ready=lambda: storage,
        agent_for_conversation=agent_for_conversation,
        model_for_message=lambda message: object(),
        capabilities_for_message=lambda message: tuple(message["display_metadata"]["capabilities"]),
        conversation_config=lambda conversation_id: {},
    )
    service = ConversationService(runtime)

    async def resolve_user(user_id, tenant_id):
        return SimpleNamespace(user_id=user_id)

    async def project_for_conversation(*args):
        return None

    service.resolve_user = resolve_user
    service.project_for_conversation = project_for_conversation
    return service, agent_calls


def test_completed_history_skips_agent_and_request_lookup():
    storage = _Storage("completed")
    service, agent_calls = _service(storage)

    result = asyncio.run(
        service.history(storage.conversation_id, "user", limit=50, before_seq=None)
    )

    assert result["pending_approval"] is None
    assert result["pending_interaction"] is None
    assert agent_calls == []
    assert "get_request_user_message" not in storage.calls


def test_failed_history_checks_checkpoint_with_original_capabilities(monkeypatch):
    storage = _Storage("failed")
    service, agent_calls = _service(storage)
    pending_calls: list[object] = []

    async def pending_interaction(agent, config):
        pending_calls.append(agent)
        return []

    monkeypatch.setattr(conversation_module, "aget_pending_interaction", pending_interaction)

    asyncio.run(service.history(storage.conversation_id, "user", limit=50, before_seq=None))

    assert agent_calls == [("ask_user",)]
    assert len(pending_calls) == 1
    assert "get_request_user_message" in storage.calls
