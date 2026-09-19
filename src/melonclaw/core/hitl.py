"""Web Human-in-the-Loop 审批与恢复逻辑。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from langchain.agents.middleware import InterruptOnConfig
from langgraph.types import Command

from melonclaw.output.formatting import sanitize_text
from melonclaw.repository.errors import UserInteractionAnswerError
from melonclaw.tool.user_input import (
    USER_INPUT_KIND,
    USER_INPUT_SCHEMA_VERSION,
    normalize_user_answer_batch,
    normalize_user_question_batch,
)

_SENSITIVE_DECISIONS = ["approve", "edit", "reject"]


SENSITIVE_TOOL_INTERRUPTS: dict[str, InterruptOnConfig] = {
    tool_name: InterruptOnConfig(
        allowed_decisions=_SENSITIVE_DECISIONS,
        description="文件操作需要人工确认后才会执行。",
    )
    for tool_name in ("write_file", "edit_file", "delete")
} | {
    "execute": InterruptOnConfig(
        allowed_decisions=_SENSITIVE_DECISIONS,
        description="Shell 命令需要人工确认后才会执行。",
    ),
}


async def aget_pending_approval(
    agent: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """异步读取仍兼容旧调用方的工具审批状态。"""

    snapshot = await agent.aget_state(config)
    pending = _pending_from_snapshot(snapshot)
    approvals = [item for item in pending or [] if item.get("kind") == "tool_approval"]
    return approvals or None


async def aget_pending_interaction(
    agent: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """异步读取 Checkpoint 中待处理的审批或用户问题。"""

    snapshot = await agent.aget_state(config)
    return _pending_from_snapshot(snapshot)


def _pending_from_snapshot(snapshot: Any) -> list[dict[str, Any]] | None:
    pending: list[dict[str, Any]] = []
    for interrupt in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(interrupt, "value", None)
        if not isinstance(value, Mapping):
            continue
        if isinstance(value.get("action_requests"), list):
            kind = "tool_approval"
        elif value.get("kind") == USER_INPUT_KIND:
            kind = USER_INPUT_KIND
        else:
            continue
        interrupt_id = getattr(interrupt, "id", None)
        if not interrupt_id:
            raise ValueError("Checkpoint interrupt 缺少真实 ID，无法安全恢复。")
        pending.append({**dict(value), "kind": kind, "id": str(interrupt_id)})
    return pending or None


def _pending_requests(request: Any) -> list[dict[str, Any]]:
    """把单个或多个 checkpoint interrupt 统一成带 ID 的请求列表。"""

    if isinstance(request, Mapping):
        interrupts = request.get("interrupts")
        if isinstance(interrupts, list):
            return [
                dict(item)
                for item in interrupts
                if isinstance(item, Mapping)
            ]
        return [dict(request)]
    if isinstance(request, (list, tuple)):
        return [dict(item) for item in request if isinstance(item, Mapping)]
    raise ValueError("HITL 请求格式无效。")


def approval_batch_id_for(request: Any, *, assistant_message_id: str) -> str:
    """为当前助手消息和 interrupt 集合生成稳定的审批批次 ID。

    审批卡片可能在浏览器刷新后重新从 Checkpoint 构造，因此不能在每次
    序列化时使用随机 UUID。把助手消息 ID 和当前 interrupt ID 集合纳入
    UUID5 后，同一批审批在刷新前后保持一致，下一批审批则会得到新 ID。
    """

    pending = _pending_requests(request)
    interrupt_ids = [str(item.get("id", "")) for item in pending]
    if not interrupt_ids or any(not interrupt_id for interrupt_id in interrupt_ids):
        raise ValueError("审批请求缺少 interrupt ID。")
    seed = ":".join(
        [
            "melonclaw:approval-batch",
            str(assistant_message_id),
            *sorted(interrupt_ids),
        ]
    )
    return str(uuid5(NAMESPACE_URL, seed))


def _pretty(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return sanitize_text(text)


def serialize_pending_approval(
    request: Any,
    *,
    approval_batch_id: str | None = None,
    assistant_message_id: str | None = None,
) -> dict[str, Any]:
    """生成可发给浏览器的审批数据，不暴露原始参数中的敏感值。"""

    serialized_interrupts: list[dict[str, Any]] = []
    for pending in _pending_requests(request):
        actions = pending.get("action_requests", [])
        reviews = pending.get("review_configs", [])
        serialized: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                continue
            review = (
                reviews[index]
                if index < len(reviews) and isinstance(reviews[index], Mapping)
                else {}
            )
            allowed = review.get("allowed_decisions", _SENSITIVE_DECISIONS)
            serialized.append(
                {
                    "name": sanitize_text(str(action.get("name", "unknown"))),
                    "description": sanitize_text(str(action.get("description", ""))),
                    "args": _pretty(action.get("args", {})),
                    "allowed_decisions": [str(choice) for choice in allowed],
                }
            )
        serialized_interrupts.append(
            {"id": str(pending.get("id", "")), "actions": serialized}
        )
    result: dict[str, Any] = {"interrupts": serialized_interrupts}
    if approval_batch_id is not None and assistant_message_id is not None:
        result.update(
            {
                "approval_batch_id": str(approval_batch_id),
                "assistant_message_id": str(assistant_message_id),
            }
        )
    # 保留单 interrupt 的旧响应形状，兼容已有 Web 客户端和书签中的页面状态。
    if len(serialized_interrupts) == 1:
        result.update(serialized_interrupts[0])
    return result


def serialize_pending_user_question(
    request: Mapping[str, Any],
    *,
    interaction_id: str,
    assistant_message_id: str,
    expires_at: str,
) -> dict[str, Any]:
    """生成可发给浏览器的问题卡片，不接受模型自带的内部 ID。"""

    payload = normalize_user_question_batch(request.get("questions"))
    questions = [
        {
            "id": sanitize_text(str(question["id"])),
            "question": sanitize_text(str(question["question"])),
            "options": [
                {
                    "id": sanitize_text(str(option["id"])),
                    "label": sanitize_text(str(option["label"])),
                    **(
                        {"description": sanitize_text(str(option["description"]))}
                        if option.get("description")
                        else {}
                    ),
                }
                for option in question["options"]
            ],
            "allow_custom_answer": bool(question["allow_custom_answer"]),
            "multi_select": bool(question["multi_select"]),
        }
        for question in payload["questions"]
    ]
    serialized = {
        "kind": USER_INPUT_KIND,
        "schema_version": USER_INPUT_SCHEMA_VERSION,
        "interaction_id": str(interaction_id),
        "interrupt_id": str(request.get("id", "")),
        "assistant_message_id": str(assistant_message_id),
        "questions": questions,
        "expires_at": expires_at,
    }
    return serialized


def _decision_groups(
    decisions: Any,
    pending: list[dict[str, Any]],
) -> dict[str, Any]:
    """解析单 interrupt 兼容格式和按 interrupt_id 分组的新格式。"""

    ids = [str(item.get("id", "")) for item in pending]
    if any(not interrupt_id for interrupt_id in ids):
        raise ValueError("HITL 请求缺少 interrupt ID。")
    if len(set(ids)) != len(ids):
        raise ValueError("HITL 请求包含重复的 interrupt ID。")

    if isinstance(decisions, Mapping):
        groups = {str(key): value for key, value in decisions.items()}
    elif isinstance(decisions, list):
        if len(pending) == 1 and not any(
            isinstance(item, Mapping) and "interrupt_id" in item
            for item in decisions
        ):
            groups = {ids[0]: decisions}
        else:
            groups = {}
            for item in decisions:
                if not isinstance(item, Mapping):
                    raise ValueError("审批决定格式无效。")
                interrupt_id = item.get("interrupt_id", item.get("id"))
                group = item.get("decisions")
                if not isinstance(interrupt_id, str) or not interrupt_id:
                    raise ValueError("多 interrupt 审批必须提供 interrupt_id。")
                if interrupt_id in groups:
                    raise ValueError("同一个 interrupt 不能重复提交决定。")
                groups[interrupt_id] = group
    else:
        raise ValueError("审批决定必须是列表或按 ID 映射。")

    if set(groups) != set(ids):
        raise ValueError("审批决定必须覆盖全部待处理 interrupt，且不能包含未知 ID。")
    return groups


def _normalize_decisions(
    request: Mapping[str, Any],
    decisions: Any,
) -> list[dict[str, Any]]:
    """校验一个 interrupt 的决定数量、权限和编辑工具名。"""

    actions = request.get("action_requests", [])
    reviews = request.get("review_configs", [])
    if not isinstance(decisions, list) or len(decisions) != len(actions):
        raise ValueError("审批决定数量与待审批工具调用数量不一致。")

    normalized: list[dict[str, Any]] = []
    for index, (action, decision) in enumerate(zip(actions, decisions, strict=True)):
        if not isinstance(action, Mapping) or not isinstance(decision, Mapping):
            raise ValueError("审批决定格式无效。")
        review = (
            reviews[index]
            if index < len(reviews) and isinstance(reviews[index], Mapping)
            else {}
        )
        allowed = set(review.get("allowed_decisions", _SENSITIVE_DECISIONS))
        decision_type = decision.get("type")
        if decision_type not in allowed:
            raise ValueError(f"工具 {action.get('name', 'unknown')} 不允许该审批决定。")

        if decision_type == "approve":
            normalized.append({"type": "approve"})
        elif decision_type == "reject":
            message = decision.get("message", "")
            if not isinstance(message, str):
                raise ValueError("reject 的 message 必须是字符串。")
            normalized.append({"type": "reject", **({"message": message} if message else {})})
        elif decision_type == "edit":
            edited_action = decision.get("edited_action")
            args = edited_action.get("args") if isinstance(edited_action, Mapping) else None
            edited_name = edited_action.get("name") if isinstance(edited_action, Mapping) else None
            if edited_name != action.get("name"):
                raise ValueError("编辑审批参数时不能修改工具名称。")
            if not isinstance(args, dict):
                raise ValueError("edit 的 edited_action.args 必须是 JSON 对象。")
            normalized.append(
                {
                    "type": "edit",
                    "edited_action": {"name": action.get("name", ""), "args": args},
                }
            )
        elif decision_type == "respond":
            message = decision.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("respond 需要提供结果内容。")
            normalized.append({"type": "respond", "message": message})
        else:
            raise ValueError(f"不支持的审批决定：{decision_type!r}。")
    return normalized


def build_resume_command(
    request: Any,
    decisions: Any,
) -> Command:
    """校验审批结果，并按 checkpoint interrupt ID 构造恢复命令。"""

    pending = _pending_requests(request)
    groups = _decision_groups(decisions, pending)
    resumes = {
        str(item["id"]): {"decisions": _normalize_decisions(item, groups[str(item["id"])])}
        for item in pending
    }
    return Command(resume=resumes)


def build_user_input_resume_command(
    request: Mapping[str, Any],
    answer: Any,
) -> tuple[Command, dict[str, Any]]:
    """校验单个用户问题答案，并按真实 interrupt ID 构造恢复命令。

    答案除 option / options / text 外，还接受 ``{"type": "cancelled"}``：
    用户主动跳过或问题过期后的服务端代答，Agent 收到后自行收尾。
    """

    if request.get("kind") != USER_INPUT_KIND:
        raise ValueError("当前 interrupt 不是用户问题。")
    normalized_question = normalize_user_question_batch(request.get("questions"))
    try:
        normalized_answer = normalize_user_answer_batch(normalized_question, answer)
    except ValueError as exc:
        # 答案不合法要落成 422 并带稳定错误码，不能混在普通 ValueError 的 400 里，
        # 否则前端只能按自然语言文案猜错在哪。
        raise UserInteractionAnswerError(str(exc)) from exc
    interrupt_id = str(request.get("id", ""))
    if not interrupt_id:
        raise ValueError("用户问题缺少 interrupt ID。")
    return Command(resume={interrupt_id: normalized_answer}), normalized_answer
