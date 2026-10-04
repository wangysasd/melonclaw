"""工具目录选择的纯函数与会话状态，不持有运行中的会话数据。"""

import hashlib
import json
from typing import Annotated, Any

from langchain.agents.middleware import AgentState
from langchain_core.messages import HumanMessage
from typing_extensions import NotRequired

FIND_TOOLS_NAME = "find_tools"
MAX_TOOL_EXPANSIONS = 4
MAX_DISCOVERED_TOOLS = 16


def append_discoveries(left: list[dict], right: list[dict]) -> list[dict]:
    """合并并行 Command 的发现结果，整个轮次共同受次数与数量上限约束。"""

    records = [*left, *right]
    if not records:
        return []
    turn = records[-1]["turn_id"]
    result = []
    names: set[str] = set()
    for record in records:
        if record["turn_id"] != turn:
            continue
        accepted = []
        for name in record["names"]:
            if name not in names and len(names) < MAX_DISCOVERED_TOOLS:
                accepted.append(name)
                names.add(name)
        result.append({"turn_id": turn, "names": accepted})
        if len(result) == MAX_TOOL_EXPANSIONS:
            break
    return result


class CatalogSelectionState(AgentState):
    tool_selection: NotRequired[dict[str, Any]]
    tool_discoveries: Annotated[list[dict[str, Any]], append_discoveries]


def selection_turn(state: Any, context: Any = None) -> str:
    """审批恢复继续使用原业务用户消息；独立框架调用使用 HumanMessage ID。"""

    message_id = getattr(context, "user_message_id", "")
    if message_id:
        return str(message_id)
    message = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    return str(message.id) if message is not None and message.id else ""


def catalog_fingerprint(tools: list[Any]) -> str:
    catalog = [(t.name, t.description, t.args) for t in tools]
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, default=str).encode()).hexdigest()
