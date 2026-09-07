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
) -> dict[str, Any] | None:
    """从当前 checkpoint 取出等待人工决定的 HITL 请求。"""

    snapshot = agent.get_state(config)
    return _pending_from_snapshot(snapshot)


async def aget_pending_approval(
    agent: Any,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """异步 Checkpointer 使用的审批状态读取版本。"""

    snapshot = await agent.aget_state(config)
    return _pending_from_snapshot(snapshot)


def _pending_from_snapshot(snapshot: Any) -> dict[str, Any] | None:
    for interrupt in snapshot.interrupts:
        value = getattr(interrupt, "value", None)
        if isinstance(value, Mapping) and isinstance(
            value.get("action_requests"), list
        ):
            return dict(value)
    return None


def _pretty(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return sanitize_text(text)


def serialize_pending_approval(request: Mapping[str, Any]) -> dict[str, Any]:
    """生成可发给浏览器的审批数据，不暴露原始参数中的敏感值。"""

    actions = request.get("action_requests", [])
    reviews = request.get("review_configs", [])
    serialized: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            continue
        review = reviews[index] if index < len(reviews) and isinstance(reviews[index], Mapping) else {}
        allowed = review.get("allowed_decisions", _SENSITIVE_DECISIONS)
        serialized.append(
            {
                "name": sanitize_text(str(action.get("name", "unknown"))),
                "description": sanitize_text(str(action.get("description", ""))),
                "args": _pretty(action.get("args", {})),
                "allowed_decisions": [str(choice) for choice in allowed],
            }
        )
    return {"actions": serialized}


def build_resume_command(
    request: Mapping[str, Any],
    decisions: Any,
) -> Command:
    """校验 Web 审批结果，并固定编辑时的工具名称。"""

    actions = request.get("action_requests", [])
    reviews = request.get("review_configs", [])
    if not isinstance(decisions, list) or len(decisions) != len(actions):
        raise ValueError("审批决定数量与待审批工具调用数量不一致。")

    normalized: list[dict[str, Any]] = []
    for index, (action, decision) in enumerate(zip(actions, decisions, strict=True)):
        if not isinstance(action, Mapping) or not isinstance(decision, Mapping):
            raise ValueError("审批决定格式无效。")
        review = reviews[index] if index < len(reviews) and isinstance(reviews[index], Mapping) else {}
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

    return Command(resume={"decisions": normalized})


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


def request_human_decision(request: Mapping[str, Any]) -> Command:
    """在 CLI 展示请求并构造可恢复该 checkpoint 的 ``Command``。"""

    actions = request.get("action_requests", [])
    reviews = request.get("review_configs", [])
    print("\n🛡️  [需要人工审批]", flush=True)
    decisions: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, Mapping):
            raise RuntimeError("HITL 请求格式无效：缺少工具调用详情。")
        review = reviews[index - 1] if index - 1 < len(reviews) else {}
        allowed = set(review.get("allowed_decisions", _SENSITIVE_DECISIONS))
        print(f"\n{index}. 工具: {sanitize_text(str(action.get('name', 'unknown')))}")
        description = action.get("description")
        if description:
            print(f"   说明: {sanitize_text(str(description))}")
        print(f"   参数:\n{_pretty(action.get('args', {}))}")
        decisions.append(_ask_decision(action, allowed))

    return Command(resume={"decisions": decisions})
