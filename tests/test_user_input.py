import ast
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from melonclaw.api.errors import error_response
from melonclaw.core.hitl import (
    _pending_from_snapshot,
    approval_batch_id_for,
    build_user_input_resume_command,
    serialize_pending_approval,
    serialize_pending_user_question,
)
from melonclaw.core.user_input import (
    USER_INPUT_CAPABILITY,
    USER_INPUT_RECOVERY_REQUIRED,
    normalize_capabilities,
    supports_user_input,
)
from melonclaw.database.schema import user_interactions as user_interactions_table
from melonclaw.middleware.user_input_guard import UserInputGuardMiddleware
from melonclaw.repository import ConversationBusyError, UserInteractionAnswerError
from melonclaw.repository.user_interaction_lifecycle import (
    ALL_STATUSES,
    ANSWERED_STATUSES,
    OPEN_STATUSES,
    UNANSWERED_STATUSES,
    UserInteractionStatus,
    can_transition,
    transition_sources,
)
from melonclaw.repository.user_interactions import (
    _archived_interrupt_id,
    _is_open_ledger,
)
from melonclaw.services import runtime as runtime_module
from melonclaw.services import user_input_execution
from melonclaw.services.conversations import _mark_recovery_required_failure
from melonclaw.services.execution_finalize import mark_interaction_recovery_required
from melonclaw.services.runtime import ChatRuntime
from melonclaw.services.user_input_execution import (
    _CANCEL_POLICY,
    CancelReason,
    UserInputExecutionService,
    _cancel_reason,
    _is_expired,
)
from melonclaw.tool.user_input import (
    normalize_user_answer_batch,
    normalize_user_question_batch,
)


async def _async_model(*args, **kwargs):
    return SimpleNamespace(cache_key=("m",))


def test_approval_batch_id_is_stable_for_same_assistant_and_interrupts():
    request = [
        {"id": "interrupt-b", "action_requests": [], "review_configs": []},
        {"id": "interrupt-a", "action_requests": [], "review_configs": []},
    ]
    first = approval_batch_id_for(request, assistant_message_id="assistant-1")
    second = approval_batch_id_for(
        list(reversed(request)),
        assistant_message_id="assistant-1",
    )
    assert first == second
    assert first != approval_batch_id_for(request, assistant_message_id="assistant-2")
    serialized = serialize_pending_approval(
        request,
        approval_batch_id=first,
        assistant_message_id="assistant-1",
    )
    assert serialized["approval_batch_id"] == first
    assert serialized["assistant_message_id"] == "assistant-1"


def question_payload():
    """一张只有一个问题的卡片：单题就是长度为一的 questions 数组。"""

    return normalize_user_question_batch(
        [
            {
                "question": "选择部署方式",
                "options": [
                    {"id": "local", "label": "本地"},
                    {"id": "cloud", "label": "云端", "description": "适合多人协作"},
                ],
                "allow_custom_answer": True,
            }
        ]
    )


def answer_for(payload, answer, question_id="question-1"):
    """把单个答案包成 batch 提交：单题卡片也必须走同一条通道。"""

    return normalize_user_answer_batch(
        payload,
        {"type": "batch", "answers": {question_id: answer}},
    )


def test_question_schema_and_answer_are_normalized():
    payload = normalize_user_question_batch(
        [
            {
                "question": "  选择部署方式 ",
                "options": question_payload()["questions"][0]["options"],
                "allow_custom_answer": True,
            }
        ]
    )
    assert payload["questions"][0]["question"] == "选择部署方式"
    assert answer_for(payload, {"type": "option", "option_id": "cloud"})["answers"][
        "question-1"
    ] == {
        "type": "option",
        "option_id": "cloud",
        "text": "云端",
    }
    assert (
        answer_for(payload, {"type": "text", "text": "先做演示"})["answers"][
            "question-1"
        ]["text"]
        == "先做演示"
    )


def test_question_schema_rejects_invalid_options_and_answers():
    with pytest.raises(ValueError, match="至少需要 2"):
        normalize_user_question_batch(
            [{"question": "问题", "options": [{"id": "one", "label": "一个"}]}]
        )
    payload = normalize_user_question_batch(
        [
            {
                "question": "问题",
                "options": question_payload()["questions"][0]["options"],
                "allow_custom_answer": False,
            }
        ]
    )
    with pytest.raises(ValueError, match="不属于当前问题"):
        answer_for(payload, {"type": "option", "option_id": "forged"})
    with pytest.raises(ValueError, match="不允许自定义"):
        answer_for(payload, {"type": "text", "text": "自定义"})


def test_multi_select_answer_is_normalized_and_validated():
    payload = normalize_user_question_batch(
        [
            {
                "question": "选择需要启用的能力",
                "options": [
                    {"id": "search", "label": "联网搜索"},
                    {"id": "files", "label": "文件处理"},
                    {"id": "memory", "label": "长期记忆"},
                ],
                "allow_custom_answer": True,
                "multi_select": True,
            }
        ]
    )
    assert payload["questions"][0]["multi_select"] is True
    assert answer_for(
        payload, {"type": "options", "option_ids": ["search", "memory"]}
    )["answers"]["question-1"] == {
        "type": "options",
        "option_ids": ["search", "memory"],
        "text": "联网搜索、长期记忆",
    }
    assert answer_for(
        payload, {"type": "options", "option_ids": [], "text": "只在工作日启用"}
    )["answers"]["question-1"] == {
        "type": "options",
        "option_ids": [],
        "text": "",
        "custom_text": "只在工作日启用",
    }
    with pytest.raises(ValueError, match="不能重复"):
        answer_for(payload, {"type": "options", "option_ids": ["search", "search"]})
    with pytest.raises(ValueError, match="必须提交 option_ids"):
        answer_for(payload, {"type": "option", "option_id": "search"})


def test_question_batch_requires_all_answers_and_resumes_once():
    payload = normalize_user_question_batch(
        [
            {
                "question": "部署环境",
                "options": [
                    {"id": "staging", "label": "测试"},
                    {"id": "prod", "label": "生产"},
                ],
            },
            {
                "question": "启用哪些能力",
                "options": [
                    {"id": "search", "label": "联网搜索"},
                    {"id": "memory", "label": "长期记忆"},
                ],
                "multi_select": True,
            },
        ]
    )
    assert [item["id"] for item in payload["questions"]] == [
        "question-1",
        "question-2",
    ]
    answer = normalize_user_answer_batch(
        payload,
        {
            "type": "batch",
            "answers": {
                "question-1": {"type": "option", "option_id": "staging"},
                "question-2": {
                    "type": "options",
                    "option_ids": ["search", "memory"],
                },
            },
        },
    )
    assert answer["answers"]["question-1"]["text"] == "测试"
    assert answer["answers"]["question-2"]["text"] == "联网搜索、长期记忆"
    request = {**payload, "id": "interrupt-batch"}
    command, normalized = build_user_input_resume_command(
        request,
        {
            "type": "batch",
            "answers": {
                "question-1": {"type": "option", "option_id": "staging"},
                "question-2": {"type": "options", "option_ids": ["search"]},
            },
        },
    )
    assert set(command.resume) == {"interrupt-batch"}
    assert normalized["type"] == "batch"
    with pytest.raises(ValueError, match="全部问题"):
        normalize_user_answer_batch(
            payload,
            {
                "type": "batch",
                "answers": {
                    "question-1": {"type": "option", "option_id": "staging"},
                },
            },
        )


def test_pending_question_keeps_real_interrupt_id_and_builds_resume_command():
    pending = question_payload()
    snapshot = SimpleNamespace(
        interrupts=[SimpleNamespace(id="interrupt-1", value=pending)]
    )
    requests = _pending_from_snapshot(snapshot)
    assert requests == [{**pending, "id": "interrupt-1"}]
    command, answer = build_user_input_resume_command(
        requests[0],
        {
            "type": "batch",
            "answers": {"question-1": {"type": "option", "option_id": "local"}},
        },
    )
    assert command.resume == {
        "interrupt-1": {
            "type": "batch",
            "answers": {
                "question-1": {"type": "option", "option_id": "local", "text": "本地"}
            },
        }
    }
    serialized = serialize_pending_user_question(
        requests[0],
        interaction_id="interaction-1",
        assistant_message_id="assistant-1",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    assert serialized["interaction_id"] == "interaction-1"
    assert serialized["interrupt_id"] == "interrupt-1"
    assert serialized["questions"][0]["multi_select"] is False
    # 单题不再重复铺一层顶层字段，只有 questions 这一个来源。
    assert "question" not in serialized
    assert "multi_select" not in serialized
    assert answer["answers"]["question-1"]["option_id"] == "local"

    with pytest.raises(ValueError, match="缺少真实 ID"):
        _pending_from_snapshot(
            SimpleNamespace(interrupts=[SimpleNamespace(value=pending)])
        )

    multi_pending = {
        **question_payload(),
        "questions": [{**question_payload()["questions"][0], "multi_select": True}],
    }
    multi_request = _pending_from_snapshot(
        SimpleNamespace(
            interrupts=[SimpleNamespace(id="interrupt-2", value=multi_pending)]
        )
    )[0]
    command, answer = build_user_input_resume_command(
        multi_request,
        {
            "type": "batch",
            "answers": {
                "question-1": {"type": "options", "option_ids": ["local", "cloud"]}
            },
        },
    )
    assert command.resume["interrupt-2"]["answers"]["question-1"]["option_ids"] == [
        "local",
        "cloud",
    ]
    assert answer["answers"]["question-1"]["text"] == "本地、云端"
    serialized_multi = serialize_pending_user_question(
        multi_request,
        interaction_id="interaction-2",
        assistant_message_id="assistant-2",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    assert serialized_multi["questions"][0]["multi_select"] is True

    batch_request = normalize_user_question_batch(
        [
            {
                "question": "部署环境",
                "options": [
                    {"id": "staging", "label": "测试"},
                    {"id": "prod", "label": "生产"},
                ],
            },
            {"question": "补充说明", "allow_custom_answer": True},
        ]
    )
    batch_request["id"] = "interrupt-3"
    serialized_batch = serialize_pending_user_question(
        batch_request,
        interaction_id="interaction-3",
        assistant_message_id="assistant-3",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    assert [item["id"] for item in serialized_batch["questions"]] == [
        "question-1",
        "question-2",
    ]


def test_cancel_answer_is_normalized_for_single_question():
    # 单题也走 batch 通道：协议里没有"单题专用"的取消形状。
    payload = question_payload()
    normalized = normalize_user_answer_batch(payload, {"type": "cancelled"})
    assert normalized["type"] == "cancelled"
    assert normalized["text"]
    command, resumed = build_user_input_resume_command(
        {**payload, "id": "interrupt-cancel"},
        {"type": "cancelled"},
    )
    assert command.resume == {"interrupt-cancel": normalized}
    assert resumed == normalized
    with pytest.raises(ValueError, match="不支持其他字段"):
        normalize_user_answer_batch(
            payload, {"type": "cancelled", "option_id": "cloud"}
        )


def test_cancel_answer_is_normalized_for_question_batch():
    payload = normalize_user_question_batch(
        [
            {
                "question": "部署环境",
                "options": [
                    {"id": "staging", "label": "测试"},
                    {"id": "prod", "label": "生产"},
                ],
            },
            {"question": "补充说明", "allow_custom_answer": True},
        ]
    )
    normalized = normalize_user_answer_batch(payload, {"type": "cancelled"})
    assert normalized["type"] == "cancelled"
    command, _ = build_user_input_resume_command(
        {**payload, "id": "interrupt-batch-cancel"},
        {"type": "cancelled"},
    )
    assert command.resume == {"interrupt-batch-cancel": normalized}


def test_expired_helper_reads_iso_timestamps():
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    assert _is_expired(past) is True
    assert _is_expired(future) is False
    with pytest.raises(ValueError):
        _is_expired("not-a-timestamp")
    with pytest.raises(ValueError, match="必须包含时区"):
        _is_expired("2030-01-01T00:00:00")


def _cancel_question() -> dict[str, Any]:
    """Checkpoint 里挂着的那个问题，取消恢复要用它构造 resume。"""

    return {**question_payload(), "id": "interrupt-1"}


class _CancelHarness:
    """能把两条取消路径真正跑到恢复的假运行时，并记录关键调用。"""

    def __init__(self, candidate: dict[str, Any] | None) -> None:
        self.candidate = candidate
        self.question = _cancel_question()
        self.calls: list[str] = []
        self.accept_calls: list[dict[str, Any]] = []
        self.assistant_updates: list[dict[str, Any]] = []
        self.streamed: list[Any] = []
        self.ledgers: dict[str, dict[str, Any]] = {}
        if candidate is not None:
            self.ledgers[candidate["id"]] = candidate
        self.assistant = {
            "id": candidate["assistant_message_id"] if candidate else str(uuid4()),
            "request_id": "request-1",
            "content": "",
            "assistant_steps": [],
            "display_metadata": {},
            "execution_duration_ms": None,
        }
        self.project = {
            "id": str(uuid4()),
            "name": "project-1",
            "workdir_path": "p1",
        }
        self.conversation_id = uuid4()

    async def pending(self, agent, config):
        self.calls.append("pending")
        return [self.question]

    def execution(self):
        async def stream_execution(prepared, agent_input=None):
            self.calls.append("stream")
            self.streamed.append(agent_input)
            yield {"type": "done"}

        return SimpleNamespace(
            runtime=SimpleNamespace(
                require_ready=lambda: self,
                settings=SimpleNamespace(user_input_ttl_seconds=60),
                worker_id="worker-1",
                conversation_config=lambda conversation_id: {
                    "configurable": {"thread_id": str(conversation_id)}
                },
                model_for_message=_async_model,
                capabilities_for_message=lambda message: (USER_INPUT_CAPABILITY,),
                agent_for_conversation=self._agent_for_conversation,
                workspace_dir=lambda conversation, project: Path(project["workdir_path"]),
            ),
            conversations=SimpleNamespace(
                resolve_user=self._resolve_user,
                project_for_conversation=self._project_for_conversation,
            ),
            stream_execution=stream_execution,
        )

    async def _agent_for_conversation(
        self, conversation, project, model, capabilities=None
    ):
        self.calls.append("agent_for_conversation")
        return SimpleNamespace(capabilities=capabilities or ())

    async def _resolve_user(self, user_id):
        return SimpleNamespace(
            user_id=user_id,
            tenant_id="tenant-1",
            tenant_name_zh="默认租户",
            tenant_role="owner",
            tenant_status="active",
        )

    async def _project_for_conversation(self, storage, conversation, context):
        return self.project

    async def get_conversation(self, conversation_id, user_id):
        self.calls.append("get_conversation")
        return {"id": str(conversation_id), "user_id": user_id}

    async def get_user_interaction_cancellation_candidate(self, conversation_id, user_id):
        self.calls.append("candidate")
        return self.candidate

    async def get_recovery_required_interaction(self, conversation_id, user_id):
        self.calls.append("recovery_ledger")
        return self.candidate

    async def get_incomplete_assistant(self, conversation_id, user_id):
        self.calls.append("incomplete_assistant")
        return self.assistant

    async def get_latest_assistant(self, conversation_id, user_id):
        self.calls.append("latest_assistant")
        return self.assistant

    async def find_request(self, conversation_id, user_id, request_id):
        return SimpleNamespace(user_message={}, attachments=[])

    async def get_user_interaction(self, conversation_id, user_id, interaction_id):
        return self.ledgers.get(str(interaction_id))

    async def create_or_get_user_interaction(
        self,
        conversation_id,
        assistant_message_id,
        user_id,
        interrupt_id,
        question,
        ttl_seconds,
    ):
        self.calls.append("create_or_get_user_interaction")
        ledger = {
            "id": str(uuid4()),
            "assistant_message_id": str(assistant_message_id),
            "user_id": user_id,
            "interrupt_id": interrupt_id,
            "payload": question,
            "status": "waiting",
            "expires_at": None,
        }
        self.ledgers[ledger["id"]] = ledger
        return ledger

    async def discard_recovery_required_interaction(self, conversation_id, interaction_id):
        self.calls.append("discard_recovery_required_interaction")
        self.ledgers.pop(str(interaction_id), None)

    async def update_assistant(
        self,
        conversation_id,
        assistant_message_id,
        *,
        status,
        display_metadata,
        error_code,
        expected_status,
    ):
        self.calls.append("update_assistant")
        self.assistant_updates.append(
            {
                "assistant_message_id": str(assistant_message_id),
                "status": status,
                "error_code": error_code,
                "expected_status": tuple(expected_status),
            }
        )

    async def try_advisory_lock(self, conversation_id):
        self.calls.append("lock")
        return "lock-connection"

    async def release_advisory_lock(self, connection, conversation_id):
        self.calls.append("unlock")

    async def accept_user_interaction(
        self,
        conversation_id,
        user_id,
        interaction_id,
        decision_request_id,
        normalized_answer,
        allow_expired=False,
    ):
        self.calls.append("accept")
        self.accept_calls.append(
            {
                "interaction_id": str(interaction_id),
                "answer": normalized_answer,
                "allow_expired": allow_expired,
            }
        )
        return (
            {
                "id": str(interaction_id),
                "decision_request_id": decision_request_id,
                "assistant_message_id": self.assistant["id"],
            },
            True,
        )


def _cancel_candidate(status: str, *, expires_at: str, stale_payload: bool = False):
    question = _cancel_question()
    return {
        "id": str(uuid4()),
        "assistant_message_id": str(uuid4()),
        "interrupt_id": "interrupt-stale" if stale_payload else question["id"],
        "payload": {"kind": "user_question"} if stale_payload else question,
        "status": status,
        "expires_at": expires_at,
    }


def test_cancel_reason_maps_every_ledger_status_to_one_policy():
    assert _cancel_reason(None) is None
    assert _cancel_reason({"status": "accepted"}) is None
    assert _cancel_reason({"status": "resolved"}) is None
    assert _cancel_reason({"status": "waiting"}) is CancelReason.EXPIRED
    assert _cancel_reason({"status": "expired"}) is CancelReason.EXPIRED
    assert _cancel_reason({"status": "recovery_required"}) is CancelReason.RECOVERY_REQUIRED
    # 加新原因时必须同时补策略，否则 KeyError 会一路崩到发消息。
    assert set(_CANCEL_POLICY) == set(CancelReason)


def test_new_message_without_cancellation_candidate_does_nothing():
    harness = _CancelHarness(None)
    service = UserInputExecutionService(harness.execution())
    assert asyncio.run(
        service.cancel_before_new_message(harness.conversation_id, "user-1")
    ) is False
    assert harness.calls == ["candidate"]


def test_expired_question_is_cancelled_in_place_on_new_message(monkeypatch):
    # 过期卡片上发新消息：代答取消必须原地接收那本过期账本。另开一本带新有效
    # 期的账本等于一次刷新就把过期问题续期了。
    candidate = _cancel_candidate(
        "expired",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    harness = _CancelHarness(candidate)
    monkeypatch.setattr(user_input_execution, "aget_pending_interaction", harness.pending)
    service = UserInputExecutionService(harness.execution())

    assert asyncio.run(
        service.cancel_before_new_message(harness.conversation_id, "user-1")
    ) is True

    # 账本只查一次：不再为了确认状态又去打一遍数据库。
    assert harness.calls.count("candidate") == 1
    assert "recovery_ledger" not in harness.calls
    assert "create_or_get_user_interaction" not in harness.calls
    assert "latest_assistant" not in harness.calls
    # 原地接收过期账本，只有服务端代答才允许。
    assert harness.accept_calls[-1]["interaction_id"] == candidate["id"]
    assert harness.accept_calls[-1]["allow_expired"] is True
    assert harness.accept_calls[-1]["answer"]["type"] == "cancelled"
    # 真的唤醒了 Checkpoint 里那个 interrupt。
    assert set(harness.streamed[-1].resume) == {harness.question["id"]}


def test_recovery_required_round_is_reopened_on_new_message(monkeypatch):
    # "答案已收但没跑完"那轮发新消息：旧账本已经失败，要换一本新账本重开，
    # 旧账本丢弃，助手状态回滚成可收尾，再走同一条取消通道。
    candidate = _cancel_candidate(
        "recovery_required",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        stale_payload=True,
    )
    harness = _CancelHarness(candidate)
    monkeypatch.setattr(user_input_execution, "aget_pending_interaction", harness.pending)
    service = UserInputExecutionService(harness.execution())

    assert asyncio.run(
        service.cancel_before_new_message(harness.conversation_id, "user-1")
    ) is True

    assert harness.calls.count("candidate") == 1
    assert "recovery_ledger" not in harness.calls
    # 失败轮次挂在最新那条助手消息上：定位查询必须先取 latest，
    # 之后 prepare 才会按常规去读 incomplete。
    assert harness.calls.index("latest_assistant") < harness.calls.index(
        "incomplete_assistant"
    )
    assert harness.calls.count("create_or_get_user_interaction") == 1
    assert harness.calls.count("discard_recovery_required_interaction") == 1
    assert harness.assistant_updates == [
        {
            "assistant_message_id": candidate["assistant_message_id"],
            "status": "interrupted",
            "error_code": None,
            "expected_status": ("failed", "cancelled"),
        }
    ]
    # 被接收的是新账本，不是那本已经失败的旧账本。
    accepted = harness.accept_calls[-1]
    assert accepted["interaction_id"] != candidate["id"]
    assert harness.ledgers[accepted["interaction_id"]]["status"] == "waiting"
    assert set(harness.streamed[-1].resume) == {harness.question["id"]}


def test_waiting_question_survives_a_new_message(monkeypatch):
    # 还在有效期内的卡片不能被"用户发了条新消息"顺手取消掉。
    candidate = _cancel_candidate(
        "waiting",
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    harness = _CancelHarness(candidate)
    monkeypatch.setattr(user_input_execution, "aget_pending_interaction", harness.pending)
    service = UserInputExecutionService(harness.execution())

    assert asyncio.run(
        service.cancel_before_new_message(harness.conversation_id, "user-1")
    ) is False
    assert harness.accept_calls == []
    assert "stream" not in harness.calls
    assert candidate["id"] in harness.ledgers


def test_closed_interaction_ledger_is_archived_and_reopened():
    # 只有仍在等待回答的账本可以复用，否则同一 interrupt 再次提问时要另开一本。
    assert _is_open_ledger({"status": "waiting"}) is True
    assert _is_open_ledger({"status": "accepted"}) is False
    assert _is_open_ledger({"status": "resolved"}) is False
    assert _is_open_ledger({"status": "expired"}) is False
    assert _is_open_ledger(None) is False

    archived = _archived_interrupt_id("interrupt-1")
    assert archived.startswith("interrupt-1#closed-")
    # 归档值必须各不相同，否则多轮追问会撞上唯一键。
    assert archived != _archived_interrupt_id("interrupt-1")


def test_invalid_answer_is_reported_as_422_with_stable_error_code():
    # 答案填错（不存在的选项、不允许自定义）要和"这一轮已经变了"区分开：
    # 前者改答案重提即可，后者必须刷新。
    request = _pending_from_snapshot(
        SimpleNamespace(interrupts=[SimpleNamespace(id="interrupt-1", value=question_payload())])
    )[0]
    with pytest.raises(UserInteractionAnswerError) as raised:
        build_user_input_resume_command(request, {"type": "option", "option_id": "forged"})
    assert raised.value.status_code == 422
    assert raised.value.error_code == "user_answer_invalid"

    response = error_response(UserInteractionAnswerError("答案不符合当前问题。"))
    assert response.status_code == 422
    assert json.loads(response.body) == {
        "error": "答案不符合当前问题。",
        "error_code": "user_answer_invalid",
    }


def test_recovery_required_marking_is_optional_and_never_masks_agent_error():
    class _Storage:
        def __init__(self, fail: bool = False) -> None:
            self.calls: list[tuple[object, object]] = []
            self.fail = fail

        async def mark_user_interaction_recovery_required(self, conversation_id, interaction_id):
            if self.fail:
                raise RuntimeError("数据库不可用")
            self.calls.append((conversation_id, interaction_id))
            return True

    conversation_id, interaction_id = uuid4(), uuid4()
    storage = _Storage()
    asyncio.run(
        mark_interaction_recovery_required(storage, conversation_id, interaction_id)
    )
    assert storage.calls == [(conversation_id, interaction_id)]

    # 新提的消息没有账本：不能凭空新建一条。
    plain = _Storage()
    asyncio.run(mark_interaction_recovery_required(plain, conversation_id, None))
    assert plain.calls == []

    # 写不进去也不能把 Agent 的原始错误顶掉。
    broken = _Storage(fail=True)
    asyncio.run(mark_interaction_recovery_required(broken, conversation_id, interaction_id))


def test_history_shows_unresolved_recovery_as_failure_not_a_question():
    # 答案已收但这一轮没跑完时，历史必须表现成失败；再亮一张卡片只会让用户
    # 点下去拿到"已经提交过不同的答案"。
    messages = [
        {"id": "assistant-1", "role": "assistant", "status": "failed", "error_code": "agent_execution_failed"},
        {"id": "user-1", "role": "user", "status": "completed", "error_code": None},
    ]
    _mark_recovery_required_failure(messages, "assistant-1")
    assert messages[0]["error_code"] == USER_INPUT_RECOVERY_REQUIRED
    assert messages[0]["status"] == "failed"
    assert messages[1]["error_code"] is None


def test_busy_conversation_is_rejected_before_any_write():
    # 会话忙就必须在写账本之前失败：先在锁外建好账本再说"忙"，等于留下一本
    # 没人认领的 open 账本，还要让浏览器收到一句不准确的错误。
    writes: list[str] = []
    assistant_message_id = uuid4()

    class _Storage:
        async def get_conversation(self, conversation_id, user_id):
            return {"id": str(conversation_id), "user_id": user_id}

        async def get_user_interaction(self, conversation_id, user_id, interaction_id):
            return None

        async def get_incomplete_assistant(self, conversation_id, user_id):
            return {"id": str(assistant_message_id), "request_id": "request-1"}

        async def find_request(self, conversation_id, user_id, request_id):
            return SimpleNamespace(user_message={}, attachments=[])

        async def try_advisory_lock(self, conversation_id):
            return None

        async def create_or_get_user_interaction(self, *args, **kwargs):
            writes.append("ledger")
            raise AssertionError("会话忙时不应写入账本")

    class _Conversations:
        async def resolve_user(self, user_id):
            return SimpleNamespace(user_id=user_id, tenant_id="tenant-1")

        async def project_for_conversation(self, storage, conversation, context):
            return {"id": "project-1", "name": "p", "workdir_path": "p1"}

    runtime = SimpleNamespace(
        require_ready=lambda: _Storage(),
        settings=SimpleNamespace(user_input_ttl_seconds=60),
        worker_id="worker-1",
        conversation_config=lambda conversation_id: {"configurable": {"thread_id": str(conversation_id)}},
        model_for_message=_async_model,
        capabilities_for_message=lambda message: (),
    )

    async def _agent_for_conversation(conversation, project, model, capabilities=None):
        return SimpleNamespace()

    runtime.agent_for_conversation = _agent_for_conversation

    execution = SimpleNamespace(runtime=runtime, conversations=_Conversations())
    service = UserInputExecutionService(execution)
    with pytest.raises(ConversationBusyError):
        asyncio.run(
            service.prepare(
                uuid4(),
                "user-1",
                uuid4(),
                assistant_message_id,
                "decision-1",
                {"type": "cancelled"},
            )
        )
    assert writes == []


def _batch_state(names: list[str]) -> dict:
    """构造“模型一次输出了这几个工具调用”的批次状态。"""

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"call-{index}"}
            for index, name in enumerate(names, start=1)
        ],
    )
    return {"messages": [message]}


def _request(state: dict, index: int):
    return SimpleNamespace(state=state, tool_call=state["messages"][0].tool_calls[index])


def test_user_input_guard_cancels_whole_mixed_batch():
    # ask_user + execute 同批：两个调用都不能执行，否则副作用会先于回答发生。
    guard = UserInputGuardMiddleware()
    executed: list[str] = []

    async def handler(request):
        executed.append(request.tool_call["name"])
        return ToolMessage(content="executed", tool_call_id=request.tool_call["id"])

    state = _batch_state(["ask_user", "execute"])
    ask_result = asyncio.run(guard.awrap_tool_call(_request(state, 0), handler))
    execute_result = asyncio.run(guard.awrap_tool_call(_request(state, 1), handler))

    assert ask_result.status == "error"
    assert execute_result.status == "error"
    assert ask_result.tool_call_id == "call-1"
    assert execute_result.tool_call_id == "call-2"
    assert "单独调用" in str(ask_result.content)
    # 整批短路：副作用工具一次都没真正执行。
    assert executed == []


def test_user_input_guard_short_circuits_sync_path():
    guard = UserInputGuardMiddleware()

    def handler(request):
        raise AssertionError("同步路径也必须被护栏拦住")

    state = _batch_state(["ask_user", "write_file"])
    result = guard.wrap_tool_call(_request(state, 1), handler)
    assert result.status == "error"
    assert result.tool_call_id == "call-2"


def test_user_input_guard_allows_single_ask_user_and_plain_batches():
    guard = UserInputGuardMiddleware()
    executed: list[str] = []

    async def handler(request):
        executed.append(request.tool_call["name"])
        return ToolMessage(content="executed", tool_call_id=request.tool_call["id"])

    single = _batch_state(["ask_user"])
    assert asyncio.run(guard.awrap_tool_call(_request(single, 0), handler)).content == "executed"
    plain = _batch_state(["write_file", "execute"])
    asyncio.run(guard.awrap_tool_call(_request(plain, 0), handler))
    assert executed == ["ask_user", "write_file"]


def test_client_capability_negotiation_normalizes_known_capability():
    assert supports_user_input(["user_input_v1", "other"]) is True
    assert supports_user_input([]) is False
    assert supports_user_input(None) is False
    # 字符串本身可迭代，但不能当成能力清单，否则 "u" 之类会被误判。
    assert supports_user_input(USER_INPUT_CAPABILITY) is False
    assert normalize_capabilities(["other"]) == ()
    assert normalize_capabilities([USER_INPUT_CAPABILITY, "other"]) == (USER_INPUT_CAPABILITY,)


def test_capabilities_are_read_back_from_message_metadata():
    runtime = ChatRuntime()
    assert runtime.capabilities_for_message(None) == ()
    assert runtime.capabilities_for_message({}) == ()
    assert (
        runtime.capabilities_for_message(
            {"display_metadata": {"capabilities": [USER_INPUT_CAPABILITY]}}
        )
        == (USER_INPUT_CAPABILITY,)
    )


def test_agent_cache_key_includes_client_capabilities(monkeypatch, tmp_path):
    # 能力必须进缓存键：同一个 Project 上，声明和未声明提问能力的客户端
    # 拿到的是两个不同工具集的 Agent，不能互相复用。
    built: list[tuple[str, ...]] = []

    async def fake_build_research_agent(settings, **kwargs):
        built.append(tuple(kwargs.get("client_capabilities") or ()))
        return SimpleNamespace(capabilities=built[-1], melonclaw_mcp_failed=False)

    monkeypatch.setattr(runtime_module, "build_research_agent", fake_build_research_agent)
    monkeypatch.setattr(ChatRuntime, "ready", property(lambda self: True))
    async def fake_resolve_model(self, *args, **kwargs):
        return SimpleNamespace(cache_key=("model-1",))

    monkeypatch.setattr(ChatRuntime, "resolve_model", fake_resolve_model)
    monkeypatch.setattr(ChatRuntime, "project_workspace_dir", lambda self, project: tmp_path)

    class FakeStorage:
        async def list_visible_skill_rows(self, user_id, **kwargs):
            return []

        async def skills_revision(self):
            return "rev"

        async def mcp_revision(self):
            return "rev"

        async def models_revision(self):
            return "rev"

        async def list_visible_mcp_rows(self, user_id):
            return []

    runtime = ChatRuntime(
        settings=SimpleNamespace(workspace_root=tmp_path, data_root=tmp_path, agent_cache_entries=4),
        storage=FakeStorage(),
        checkpointer=object(),
        memory_service=object(),
        workspace_agents={},
    )
    conversation = {"id": str(uuid4()), "user_id": "user-1"}
    project = {"id": "project-1", "workdir_path": "p1"}
    with_capability = asyncio.run(
        runtime.agent_for_conversation(
            conversation, project, capabilities=[USER_INPUT_CAPABILITY]
        )
    )
    without_capability = asyncio.run(
        runtime.agent_for_conversation(conversation, project, capabilities=[])
    )
    again = asyncio.run(
        runtime.agent_for_conversation(
            conversation, project, capabilities=[USER_INPUT_CAPABILITY]
        )
    )

    assert built == [(USER_INPUT_CAPABILITY,), ()]
    assert again is with_capability
    assert without_capability is not with_capability


def test_ordinary_conversation_uses_its_own_workspace(monkeypatch, tmp_path):
    built_workspaces: list[Path] = []

    async def fake_build_research_agent(settings, **kwargs):
        built_workspaces.append(Path(kwargs["workspace_dir"]))
        return SimpleNamespace(melonclaw_mcp_failed=False)

    monkeypatch.setattr(runtime_module, "build_research_agent", fake_build_research_agent)
    monkeypatch.setattr(ChatRuntime, "ready", property(lambda self: True))
    async def fake_resolve_model(self, *args, **kwargs):
        return SimpleNamespace(cache_key=("model-1",))

    monkeypatch.setattr(
        ChatRuntime,
        "resolve_model",
        fake_resolve_model,
    )

    class FakeStorage:
        async def list_visible_skill_rows(self, user_id, **kwargs):
            return []

        async def skills_revision(self):
            return "rev"

        async def mcp_revision(self):
            return "rev"

        async def models_revision(self):
            return "rev"

        async def list_visible_mcp_rows(self, user_id):
            return []

    runtime = ChatRuntime(
        settings=SimpleNamespace(workspace_root=tmp_path, agent_cache_entries=4, data_root=tmp_path),
        storage=FakeStorage(),
        checkpointer=object(),
        memory_service=object(),
        workspace_agents={},
    )
    conversation_id = uuid4()
    conversation = {"id": str(conversation_id), "user_id": "user-1"}

    asyncio.run(runtime.agent_for_conversation(conversation, None))

    assert built_workspaces == [tmp_path / "conversations" / str(conversation_id)]
    assert built_workspaces[0].is_dir()


# ---- 账本状态生命周期：转换表是唯一事实来源 ----


def test_lifecycle_schema_check_constraint_matches_statuses():
    """schema.py 的 CHECK 约束必须与生命周期常量一一对应，防止两边漂移。"""

    constraint = next(
        item
        for item in user_interactions_table.constraints
        if getattr(item, "name", "") == "ck_user_interactions_status"
    )
    listed = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
    assert listed == set(ALL_STATUSES)
    default = user_interactions_table.c.status.server_default
    assert default is not None
    assert str(default.arg) == UserInteractionStatus.WAITING


def test_lifecycle_terminal_statuses_are_closed():
    """resolved / discarded 是终态：账本是审计记录，关闭后不回滚。"""

    for terminal in (UserInteractionStatus.RESOLVED, UserInteractionStatus.DISCARDED):
        assert all(not can_transition(terminal, other) for other in ALL_STATUSES)


def test_lifecycle_transition_sources_match_expected_edges():
    assert transition_sources(UserInteractionStatus.ACCEPTED) == frozenset(
        {UserInteractionStatus.WAITING, UserInteractionStatus.EXPIRED}
    )
    assert transition_sources(UserInteractionStatus.RESOLVED) == frozenset(
        {UserInteractionStatus.ACCEPTED}
    )
    assert transition_sources(UserInteractionStatus.RECOVERY_REQUIRED) == frozenset(
        {UserInteractionStatus.ACCEPTED}
    )
    assert transition_sources(UserInteractionStatus.DISCARDED) == frozenset(
        {UserInteractionStatus.RECOVERY_REQUIRED}
    )
    # waiting 是唯一起点，没有状态能变回 waiting。
    assert transition_sources(UserInteractionStatus.WAITING) == frozenset()


def test_lifecycle_can_transition_agrees_with_sources():
    """can_transition 与 transition_sources 必须互为反面，防止手改漂移。"""

    for source in ALL_STATUSES:
        for target in ALL_STATUSES:
            assert can_transition(source, target) is (
                source in transition_sources(target)
            )


def test_lifecycle_semantic_groups_match_question_flow():
    assert OPEN_STATUSES == frozenset(
        {UserInteractionStatus.WAITING, UserInteractionStatus.ACCEPTED}
    )
    assert ANSWERED_STATUSES == frozenset(
        {UserInteractionStatus.ACCEPTED, UserInteractionStatus.RESOLVED}
    )
    assert UNANSWERED_STATUSES == frozenset(
        {UserInteractionStatus.WAITING, UserInteractionStatus.EXPIRED}
    )


def test_cancel_reason_values_match_ledger_statuses():
    """取消原因的值就是账本状态，不允许两处各写一份字面量。"""

    assert CancelReason.EXPIRED == UserInteractionStatus.EXPIRED
    assert CancelReason.RECOVERY_REQUIRED == UserInteractionStatus.RECOVERY_REQUIRED


def _string_literals(tree: ast.Module) -> set[str]:
    """收集 AST 里的全部字符串字面量；docstring 不算。"""

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _reason_code_literals(tree: ast.Module) -> set[str]:
    """收集作为 ``reason_code=`` 传参的字符串。

    它们与状态字面量撞名（如 ``recovery_required``）纯属巧合：原因代码
    已经写进数据库历史，语义上也不是状态，因此只在关键字位置豁免。
    """

    excluded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "reason_code":
            if (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                excluded.add(node.value.value)
    return excluded


def test_status_literals_do_not_leak_back_into_callers():
    """账本状态字面量只允许写在生命周期模块与 database 层的 DDL 文本里。"""

    package_root = Path(__file__).resolve().parents[1] / "src" / "melonclaw"
    scanned = [
        package_root / "repository" / "user_interactions.py",
        package_root / "services" / "user_input_execution.py",
        package_root / "services" / "execution_finalize.py",
    ]
    for path in scanned:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        leaked = (
            _string_literals(tree) - _reason_code_literals(tree)
        ) & set(ALL_STATUSES)
        assert not leaked, (
            f"{path.name} 出现状态字面量 {sorted(leaked)}，"
            "请改用 user_interaction_lifecycle 的常量"
        )
