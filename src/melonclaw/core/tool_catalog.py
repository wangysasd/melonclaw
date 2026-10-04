"""按需工具请求与会话工具池的 Checkpoint 状态。"""

import hashlib
import json
from typing import Annotated, Any

from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import HumanMessage
from typing_extensions import NotRequired

FIND_TOOLS_NAME = "find_tools"


def append_requests(left: list[dict], right: list[dict]) -> list[dict]:
    """合并并行请求，按工具调用 ID 去重；执行预算由 middleware 统一控制。"""
    records = [*left, *right]
    if not records:
        return []
    turn = records[-1]["turn_id"]
    result = []
    seen = set()
    for record in records:
        if record["turn_id"] == turn and record["id"] not in seen:
            result.append(record)
            seen.add(record["id"])
    return result


class CatalogSelectionState(AgentState):
    tool_pool: NotRequired[Annotated[dict[str, Any], PrivateStateAttr]]
    tool_requests: Annotated[list[dict[str, Any]], PrivateStateAttr, append_requests]


def selection_turn(state: Any, context: Any = None) -> str:
    """审批恢复使用原业务用户消息；独立框架调用使用 HumanMessage ID。"""
    message_id = getattr(context, "user_message_id", "")
    if message_id:
        return str(message_id)
    message = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    return str(message.id) if message is not None and message.id else ""


def catalog_fingerprint(tools: list[Any]) -> str:
    catalog = sorted((t.name, t.description, t.args) for t in tools)
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, default=str).encode()).hexdigest()
