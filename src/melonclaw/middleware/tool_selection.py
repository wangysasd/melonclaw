"""官方负责选择；项目控制按需触发、会话池和目录失效。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, LLMToolSelectorMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import ensure_config, merge_configs, set_config_context
from langgraph.constants import TAG_NOSTREAM
from langgraph.types import Overwrite

from melonclaw.core.tool_catalog import (
    FIND_TOOLS_NAME,
    CatalogSelectionState,
    catalog_fingerprint,
    selection_turn,
)


class ToolPoolMiddleware(AgentMiddleware):
    """只在 find_tools 请求后选择，池存在 Checkpoint 而非共享实例中。"""

    state_schema = CatalogSelectionState

    def __init__(
        self, *, model: BaseChatModel, catalog_tools: list[Any], pool_size: int = 16,
        selection_size: int = 8, max_requests: int = 4, timeout_seconds: float = 10,
    ) -> None:
        super().__init__()
        if min(pool_size, selection_size, max_requests, timeout_seconds) <= 0:
            raise ValueError("工具池参数必须为正数。")
        self.catalog_tools = list(catalog_tools)
        self.catalog_names = frozenset(t.name for t in catalog_tools)
        self.fingerprint = catalog_fingerprint(catalog_tools)
        self.pool_size = pool_size
        self.max_requests = max_requests
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.selector = LLMToolSelectorMiddleware(model=model, max_tools=min(selection_size, pool_size))

    async def abefore_model(self, state, runtime):
        turn = selection_turn(state, runtime.context)
        previous = state.get("tool_pool")
        requests = [r for r in state.get("tool_requests", []) if r["turn_id"] == turn]
        pool = {"fingerprint": self.fingerprint, "turn_id": turn, "names": [], "processed": [], "outcome": "idle"}
        if previous and previous["fingerprint"] == self.fingerprint:
            pool["names"] = list(previous["names"])
            if previous["turn_id"] == turn:
                pool["processed"] = list(previous["processed"])
                pool["outcome"] = previous["outcome"]
        elif previous:
            # 旧目录请求不能在新目录下自动重放；新 find_tools 调用才重新选择。
            pool["processed"] = [r["id"] for r in requests]
        pending = [r for r in requests[:self.max_requests] if r["id"] not in pool["processed"]]
        receipts = []
        if pending:
            selected, results = [], {}
            semantic = []
            for request in pending:
                names = request.get("tool_names", [])
                if not names:
                    semantic.append(request)
                elif len(names) > self.pool_size:
                    results[request["id"]] = ([], "capacity_exceeded")
                elif any(name not in self.catalog_names for name in names):
                    results[request["id"]] = ([], "unavailable")
                else:
                    selected.extend(names)
                    results[request["id"]] = (names, "selected")
            outcome = "selected"
            if semantic:
                found, outcome = await self._select(state, runtime, semantic)
                selected.extend(found)
                for request in semantic:
                    results[request["id"]] = (found, outcome if found or outcome == "degraded" else "no_match")
            selected = list(dict.fromkeys(selected))
            # 新结果在列表末尾；扩充时优先保留最近选择/使用过的工具。
            pool["names"] = [n for n in pool["names"] if n not in selected] + selected
            pool["names"] = pool["names"][-self.pool_size:]
            pool["processed"].extend(r["id"] for r in pending)
            pool["outcome"] = outcome
            for request in pending:
                names, status = results[request["id"]]
                visible = [name for name in names if name in pool["names"]]
                if len(visible) != len(names):
                    status = "capacity_exceeded"
                receipts.append(self._receipt(request, status, visible, pool["names"]))
        overflow = [r for r in requests[self.max_requests:] if r["id"] not in pool["processed"]]
        for request in overflow:
            receipts.append(self._receipt(request, "exhausted", [], pool["names"]))
            pool["processed"].append(request["id"])
        update = {"tool_pool": pool}
        if receipts:
            update["messages"] = receipts
        if previous is None or previous["turn_id"] != turn:
            update["tool_requests"] = Overwrite(requests)
        return update

    @staticmethod
    def _receipt(request, status, names, visible):
        hints = {
            "selected": "工具已加载，请直接调用；尚未执行任何业务工具。",
            "no_match": "未找到匹配工具；尚未执行任何业务工具。",
            "degraded": "工具选择失败或超时；尚未执行任何业务工具。可根据目录真实名称精确加载。",
            "unavailable": "指定名称不在当前授权目录，本次精确加载全部拒绝；请重新读取真实目录。",
            "capacity_exceeded": "请求超出工具池容量，部分或全部工具未加载；请分批加载并调用。",
            "exhausted": "本消息检索额度已用完；请使用当前可见工具或说明能力不足。",
        }
        return ToolMessage(
            id=f"tool-discovery:{request['id']}", tool_call_id=request["id"], name=FIND_TOOLS_NAME,
            content=json.dumps({"status": status, "added_tools": names, "visible_tools": list(visible),
                                "hint": hints[status]}, ensure_ascii=False),
        )

    async def _select(self, state, runtime, pending):
        activity_id = str(uuid4())
        started = perf_counter()
        writer = runtime.stream_writer
        writer({"type": "run_phase", "phase": "selecting_tools"})
        writer({"type": "run_activity", "id": activity_id, "kind": "selection", "status": "started"})
        selected = []
        outcome = "selected"

        async def capture(request):
            selected.extend(t.name for t in request.tools)
            return ModelResponse(result=[])

        request = ModelRequest(
            model=self.model, tools=self.catalog_tools, state=state, runtime=runtime,
            messages=[HumanMessage(content="需要补充的工具能力：\n" + "\n".join(r["query"] for r in pending))],
        )
        try:
            config = merge_configs(ensure_config(), {"tags": ["tool-selector", TAG_NOSTREAM],
                                                     "metadata": {"tool_selector": True}})
            # 官方内部自行调用模型；只在该异步任务上下文附加 nostream 标签。
            with set_config_context(config) as context:
                async with asyncio.timeout(self.timeout_seconds):
                    await asyncio.create_task(self.selector.awrap_model_call(request, capture), context=context)
        except Exception:  # noqa: BLE001 - 只读选择失败保留旧池；取消继续向上传播
            selected = []
            outcome = "degraded"
        writer({"type": "run_activity", "id": activity_id, "kind": "selection", "status": "completed",
                "duration_ms": int((perf_counter() - started) * 1000), "outcome": outcome})
        writer({"type": "run_phase", "phase": "waiting_model"})
        return selected, outcome

    def filter_request(self, request: ModelRequest) -> ModelRequest:
        """摘要与主模型共用同一份 Checkpoint 可见工具规则。"""
        active = set(request.state["tool_pool"]["names"])
        tools = [t for t in request.tools if isinstance(t, dict) or t.name not in self.catalog_names or t.name in active]
        return request.override(tools=tools)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ):
        return await handler(self.filter_request(request))

    async def aafter_model(self, state, runtime):
        pool = state["tool_pool"]
        last = state["messages"][-1]
        used = [c["name"] for c in last.tool_calls if c["name"] in pool["names"]] if isinstance(last, AIMessage) else []
        if not used:
            return None
        names = [n for n in pool["names"] if n not in used] + list(dict.fromkeys(used))
        return {"tool_pool": {**pool, "names": names}}
