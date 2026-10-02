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
}
SENSITIVE_TOOL_INTERRUPTS["confirm_skill_install"] = InterruptOnConfig(
    allowed_decisions=["approve", "reject"],
    description=(
        "请确认 Skill 的名称、来源、安装范围和启用选项。个人资源仅自己可用；"
        "共享资源启用后对全员开放。只安装时保持停用。"
        "来源与内容摘要将在提交时再次核验；不会执行包内脚本。"
    ),
)
SENSITIVE_TOOL_INTERRUPTS["test_mcp_install"] = InterruptOnConfig(
    allowed_decisions=["approve", "reject"],
    description="确认向清单中的 MCP 地址发送连接请求和已提供凭据；仅发现工具，不调用工具。",
)
SENSITIVE_TOOL_INTERRUPTS["confirm_mcp_install"] = InterruptOnConfig(
    allowed_decisions=["approve", "reject"],
    description="确认 MCP 名称、个人归属、地址、工具白名单及启用选项。仅安装到当前用户，下一条消息生效。",
)
# prepare_mcp_install 仅暂存受控草稿、不访问远端或发布资源，无需逐次审批。
# MCP 安装、测试与准备均不进入 PTC；测试会发送凭据，安装会写入数据库。
# prepare_skill_creation 不进审批：仅在受控 tmp 暂存至多 32 文件/64000 字节，
# 不发布有效资源、不执行代码。正式保存仍由 confirm_skill_install 审批，两者不进 PTC。
# TODO(sandbox): 沙箱落地后恢复 execute 审批。过渡期 execute 免审批，
# 仅限本机单用户 127.0.0.1 开发使用；LocalShellBackend 不是安全沙箱。
# | {
#     "execute": InterruptOnConfig(
#         allowed_decisions=_SENSITIVE_DECISIONS,
#         description="Shell 命令需要人工确认后才会执行。",
#     ),
# }


async def aget_pending_approval(
    agent: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """异步读取 Checkpoint 中待处理的工具审批。"""

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


def _pending_requests(request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验当前 Checkpoint interrupt 列表。"""

    if not isinstance(request, list) or any(
        not isinstance(item, Mapping) for item in request
    ):
        raise ValueError("HITL 请求格式无效。")
    return [dict(item) for item in request]


def approval_batch_id_for(request: list[dict[str, Any]], *, assistant_message_id: str) -> str:
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
    request: list[dict[str, Any]],
    *,
    approval_batch_id: str | None = None,
    assistant_message_id: str | None = None,
) -> dict[str, Any]:
    """生成可发给浏览器的审批数据，不暴露原始参数中的敏感值。"""

    serialized_interrupts: list[dict[str, Any]] = []
    for pending in _pending_requests(request):
        actions = pending["action_requests"]
        reviews = pending["review_configs"]
        serialized: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                continue
            review = reviews[index]
            allowed = review["allowed_decisions"]
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
    """解析当前单 interrupt 平铺或多 interrupt 分组格式。"""

    ids = [str(item.get("id", "")) for item in pending]
    if any(not interrupt_id for interrupt_id in ids):
        raise ValueError("HITL 请求缺少 interrupt ID。")
    if len(set(ids)) != len(ids):
        raise ValueError("HITL 请求包含重复的 interrupt ID。")

    if not isinstance(decisions, list):
        raise ValueError("审批决定必须是列表或按 ID 映射。")
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
            interrupt_id = item.get("interrupt_id")
            group = item.get("decisions")
            if not isinstance(interrupt_id, str) or not interrupt_id:
                raise ValueError("多 interrupt 审批必须提供 interrupt_id。")
            if interrupt_id in groups:
                raise ValueError("同一个 interrupt 不能重复提交决定。")
            groups[interrupt_id] = group

    if set(groups) != set(ids):
        raise ValueError("审批决定必须覆盖全部待处理 interrupt，且不能包含未知 ID。")
    return groups


def _normalize_decisions(
    request: Mapping[str, Any],
    decisions: Any,
) -> list[dict[str, Any]]:
    """校验一个 interrupt 的决定数量、权限和编辑工具名。"""

    actions = request["action_requests"]
    reviews = request["review_configs"]
    if not isinstance(decisions, list) or len(decisions) != len(actions):
        raise ValueError("审批决定数量与待审批工具调用数量不一致。")

    normalized: list[dict[str, Any]] = []
    for index, (action, decision) in enumerate(zip(actions, decisions, strict=True)):
        if not isinstance(action, Mapping) or not isinstance(decision, Mapping):
            raise ValueError("审批决定格式无效。")
        review = reviews[index]
        allowed = set(review["allowed_decisions"])
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


def mcp_interrupts(tools):
    """动态 MCP 工具均进入审批；不相信远端 readOnlyHint，不加入 PTC。"""
    return {
        tool.name: InterruptOnConfig(allowed_decisions=_SENSITIVE_DECISIONS,
                                     description="外部 MCP 工具调用需要确认。")
        for tool in tools if getattr(tool, "metadata", None) and tool.metadata.get("mcp")
    }
