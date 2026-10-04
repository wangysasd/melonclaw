"""提交当前缺失的工具能力请求；不调用候选工具或改变权限。"""

import json
from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import Field

from melonclaw.core.tool_catalog import FIND_TOOLS_NAME, selection_turn


def build_tool_discovery(max_requests: int = 4):
    """请求存入 Checkpoint，由 middleware 统一处理并行批次预算。"""

    @tool(FIND_TOOLS_NAME)
    def find_tools(
        query: Annotated[str, Field(min_length=1, max_length=512)],
        runtime: ToolRuntime,
    ) -> Command:
        """当前可见工具不足时，描述缺少的具体能力。官方选择器下一轮扩充工具池。"""
        query = query.strip()
        if not query:
            raise ValueError("工具需求不能为空。")
        turn = selection_turn(runtime.state, runtime.context)
        requests = [r for r in runtime.state.get("tool_requests", []) if r["turn_id"] == turn]
        accepted = len(requests) < max_requests
        content = json.dumps({
            "status": "queued" if accepted else "exhausted",
            "hint": "需求已提交；下一轮以可见工具列表为准。没有合适工具时如实说明。"
                    if accepted else "本消息检索额度已用完，请使用当前工具或说明能力不足。",
        }, ensure_ascii=False)
        return Command(update={
            "tool_requests": [{"turn_id": turn, "id": runtime.tool_call_id, "query": query}] if accepted else [],
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name=FIND_TOOLS_NAME)],
        })

    return find_tools
