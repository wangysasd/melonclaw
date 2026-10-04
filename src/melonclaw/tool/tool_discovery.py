"""从当前已授权目录检索并激活本轮工具；不访问外部服务或改变权限。"""

import json
import re
from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import Field

from melonclaw.core.tool_catalog import (
    FIND_TOOLS_NAME,
    MAX_DISCOVERED_TOOLS,
    MAX_TOOL_EXPANSIONS,
    selection_turn,
)


def build_tool_discovery(catalog: list[Any]):
    """闭包仅持有本次 Agent 构建的允许目录，会话数据只通过 ToolRuntime 获取。"""

    @tool(FIND_TOOLS_NAME)
    def find_tools(
        query: Annotated[str, Field(min_length=1, max_length=512)],
        runtime: ToolRuntime,
    ) -> Command:
        """检索当前可用工具并在后续调用中启用。缺少工具时按名称或用途查询；每轮最多四次。"""

        turn = selection_turn(runtime.state, runtime.context)
        discoveries = [d for d in runtime.state.get("tool_discoveries", []) if d["turn_id"] == turn]
        active = {name for d in discoveries for name in d["names"]}
        found = []
        if len(discoveries) < MAX_TOOL_EXPANSIONS and len(active) < MAX_DISCOVERED_TOOLS:
            words = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2}", query.lower()))
            ranked = []
            for item in catalog:
                description = f"{item.name} {item.description}".lower()
                score = sum(len(w) for w in words if w in description)
                if item.name.lower() in query.lower():
                    score += 100
                if score and item.name not in active:
                    ranked.append((score, item.name, item))
            ranked.sort(key=lambda row: (-row[0], row[1]))
            found = [item for _, _, item in ranked[:min(8, MAX_DISCOVERED_TOOLS - len(active))]]
        names = [item.name for item in found]
        exhausted = len(discoveries) >= MAX_TOOL_EXPANSIONS or len(active) >= MAX_DISCOVERED_TOOLS
        content = json.dumps({
            "tools": [{"name": t.name, "description": (t.description or "")[:500]} for t in found],
            "activated": names,
            "remaining_searches": max(0, MAX_TOOL_EXPANSIONS - len(discoveries) - 1),
            "hint": "本轮检索额度已用完，请使用已启用工具。" if exhausted else "候选工具申请在下一轮启用；并行检索共享预算，以可见工具列表为准。没有匹配时换用名称或具体关键词。",
        }, ensure_ascii=False)
        return Command(update={
            "tool_discoveries": [] if exhausted else [{"turn_id": turn, "names": names}],
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name=FIND_TOOLS_NAME)],
        })

    return find_tools
