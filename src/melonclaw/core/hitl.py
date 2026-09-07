"""终端中的 Human-in-the-Loop 审批与恢复逻辑。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import InterruptOnConfig
from langgraph.types import Command

from melonclaw.output.streaming import sanitize_text


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


def get_pending_approval(
    agent: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """从当前 checkpoint 取出等待人工决定的 HITL 请求。"""

    snapshot = agent.get_state(config)
    return _pending_from_snapshot(snapshot)


async def aget_pending_approval(
    agent: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """异步 Checkpointer 使用的审批状态读取版本。"""

    snapshot = await agent.aget_state(config)
    return _pending_from_snapshot(snapshot)


def _pending_from_snapshot(snapshot: Any) -> list[dict[str, Any]] | None:
    pending: list[dict[str, Any]] = []
    for index, interrupt in enumerate(getattr(snapshot, "interrupts", ()) or ()):
        value = getattr(interrupt, "value", None)
        if isinstance(value, Mapping) and isinstance(
            value.get("action_requests"), list
        ):
            interrupt_id = getattr(interrupt, "id", None)
            if not interrupt_id:
                # 旧 checkpoint 可能没有暴露 id；保留一个稳定的兼容标识，
                # 但新版本的 LangGraph Interrupt 始终会提供真实 ID。
                interrupt_id = f"legacy-{index}"
            pending.append({**dict(value), "id": str(interrupt_id)})
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


def _pretty(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return sanitize_text(text)


def serialize_pending_approval(request: Any) -> dict[str, Any]:
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
    # 保留单 interrupt 的旧响应形状，兼容已有 Web 客户端和书签中的页面状态。
    if len(serialized_interrupts) == 1:
        result.update(serialized_interrupts[0])
    return result


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


def _ask_edit(action: Mapping[str, Any]) -> dict[str, Any]:
    while True:
        raw_args = input("新的参数 JSON> ").strip()
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            print(f"参数不是合法 JSON：{exc.msg}。请重试。")
            continue
        if not isinstance(args, dict):
            print("参数必须是 JSON 对象。请重试。")
            continue
        return {
            "type": "edit",
            "edited_action": {"name": action.get("name", ""), "args": args},
        }


def _ask_decision(action: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    choices = "/".join(
        choice
        for choice in ("approve", "edit", "reject", "respond")
        if choice in allowed
    )
    while True:
        raw = input(f"决定 [{choices}]> ").strip().lower()
        aliases = {"a": "approve", "e": "edit", "r": "reject", "s": "respond"}
        decision_type = aliases.get(raw, raw)
        if decision_type not in allowed:
            print("请输入列出的决定或其首字母。")
            continue
        if decision_type == "approve":
            return {"type": "approve"}
        if decision_type == "edit":
            return _ask_edit(action)
        if decision_type == "reject":
            message = input("拒绝原因（可选）> ").strip()
            return {"type": "reject", **({"message": message} if message else {})}
        message = input("作为工具结果返回给 Agent 的内容> ").strip()
        if message:
            return {"type": "respond", "message": message}
        print("respond 需要提供结果内容。")


def request_human_decision(request: Any) -> Command:
    """在 CLI 展示请求并构造可恢复该 checkpoint 的 ``Command``。"""

    print("\n🛡️  [需要人工审批]", flush=True)
    pending = _pending_requests(request)
    decisions_by_interrupt: dict[str, list[dict[str, Any]]] = {}
    action_number = 0
    for pending_request in pending:
        interrupt_id = str(pending_request.get("id", ""))
        actions = pending_request.get("action_requests", [])
        reviews = pending_request.get("review_configs", [])
        decisions: list[dict[str, Any]] = []
        print(f"\ninterrupt: {interrupt_id}")
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                raise RuntimeError("HITL 请求格式无效：缺少工具调用详情。")
            action_number += 1
            review = reviews[index] if index < len(reviews) else {}
            allowed = set(review.get("allowed_decisions", _SENSITIVE_DECISIONS))
            print(f"\n{action_number}. 工具: {sanitize_text(str(action.get('name', 'unknown')))}")
            description = action.get("description")
            if description:
                print(f"   说明: {sanitize_text(str(description))}")
            print(f"   参数:\n{_pretty(action.get('args', {}))}")
            decisions.append(_ask_decision(action, allowed))
        decisions_by_interrupt[interrupt_id] = decisions

    if len(pending) == 1:
        resume_decisions: Any = decisions_by_interrupt[str(pending[0].get("id", ""))]
    else:
        resume_decisions = [
            {
                "interrupt_id": str(pending_request.get("id", "")),
                "decisions": decisions_by_interrupt[str(pending_request.get("id", ""))],
            }
            for pending_request in pending
        ]
    return build_resume_command(request, resume_decisions)
