"""根 Agent assistant steps 的纯内存投影与终态归约。

一个 step 对应一次根 Agent 的 AIMessage。这个模块只处理展示层需要的安全快照，
不读取数据库，也不依赖 Web 层；事件适配器生成增量，执行服务复用同一快照落库。
"""

from __future__ import annotations

import copy
from collections import OrderedDict
from time import time
from typing import Any

from melonclaw.output.content import content_to_text
from melonclaw.output.formatting import _decode_tool_args, _preview, sanitize_text
from melonclaw.output.visible_text import VisibleTextFilter, visible_text

STEP_STATUSES = frozenset({"streaming", "running", "completed", "failed", "waiting", "unknown"})
TOOL_STATUSES = frozenset({"running", "completed", "failed", "waiting", "unknown"})
MAX_STEP_TEXT = 120 * 1024
MAX_STEPS_BYTES = 512 * 1024
MAX_PENDING_CALLS = 128


def _now_ms() -> int:
    """统一使用 epoch 毫秒：前端只用它算工具耗时，不参与业务状态判断。"""

    return int(time() * 1000)


def _copy_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return copy.deepcopy(steps or [])


def _text(value: Any) -> str:
    return sanitize_text(visible_text(content_to_text(value)))


def _tool_snapshot(
    call_id: str,
    name: str,
    batch_index: int,
    *,
    args: Any = None,
    args_preview: str | None = None,
    result_preview: str | None = None,
    status: str = "running",
    error: str | None = None,
    started_at: int | None = None,
    completed_at: int | None = None,
) -> dict[str, Any]:
    tool_status = status if status in TOOL_STATUSES else "unknown"
    result = {
        "call_id": call_id,
        "name": sanitize_text(name) or "unknown",
        "batch_index": batch_index,
        "status": tool_status,
    }
    if args_preview is not None:
        result["args_preview"] = _preview(args_preview)
    elif args is not None:
        result["args_preview"] = _preview(_decode_tool_args(args))
    if result_preview is not None:
        result["result_preview"] = _preview(result_preview)
    if error:
        result["error"] = sanitize_text(str(error))
    # 只在确实观测到调用/结果时写入时间戳；没有依据就不写，前端据此不显示耗时。
    if started_at is not None:
        result["started_at"] = int(started_at)
    if completed_at is not None:
        result["completed_at"] = int(completed_at)
    return result


def _step_status_from_tools(
    tool_calls: list[dict[str, Any]],
    fallback: str,
) -> str:
    if not tool_calls:
        return fallback
    statuses = {str(tool.get("status", "unknown")) for tool in tool_calls}
    for status in ("running", "waiting", "failed", "unknown"):
        if status in statuses:
            return status
    return "completed"


class AssistantStepAccumulator:
    """维护一条 assistant 消息内的有序 steps。

    ``project_*`` 方法用于输出 SSE，同时更新本地快照；``apply_event`` 用于执行
    服务消费已经投影出的事件。两条路径都只保留过滤、脱敏和限长后的内容。
    """

    def __init__(
        self,
        *,
        message_id: str,
        run_id: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        self.message_id = message_id
        self.run_id = run_id or message_id
        self.steps = _copy_steps(steps)
        # 通常只增长正文；保守估算新增 repr 字节，接近上限才精确扫描。
        self._total_bytes = len(str(self.steps).encode("utf-8"))
        self._filters: dict[str, VisibleTextFilter] = {}
        self._had_delta: set[str] = set()
        self._current_step_id: str | None = None
        self._tool_step_id: str | None = None
        self._call_to_step: dict[str, str] = {}
        self._pending_calls: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._pending_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._hydrate_indexes()

    def _hydrate_indexes(self) -> None:
        for step in self.steps:
            step_id = str(step.get("id", ""))
            if not step_id:
                continue
            for tool in step.get("tool_calls", []) or []:
                call_id = str(tool.get("call_id", ""))
                if call_id:
                    self._call_to_step[call_id] = step_id
                    if tool.get("status") in {"running", "waiting"}:
                        self._tool_step_id = step_id
            self._current_step_id = step_id

    def _step(self, step_id: str) -> dict[str, Any] | None:
        return next((item for item in self.steps if item.get("id") == step_id), None)

    def _step_for_call(self, call_id: str) -> dict[str, Any] | None:
        step_id = self._call_to_step.get(call_id)
        if step_id:
            return self._step(step_id)
        current = self._step(self._current_step_id) if self._current_step_id else None
        if current is not None and current.get("status") == "streaming":
            return current
        if self._tool_step_id:
            return self._step(self._tool_step_id)
        return None

    def _bounded_pending(self, target: OrderedDict[str, dict[str, Any]], key: str, value: dict[str, Any]) -> None:
        target[key] = value
        while len(target) > MAX_PENDING_CALLS:
            target.popitem(last=False)

    def start_step(self, *, source_message_id: str | None = None) -> dict[str, Any]:
        ordinal = max((int(item.get("ordinal", -1)) for item in self.steps), default=-1) + 1
        step_id = f"{self.run_id}:step:{ordinal}"
        step = {
            "id": step_id,
            "ordinal": ordinal,
            "content": "",
            "status": "streaming",
            "is_final": False,
            "tool_calls": [],
        }
        if source_message_id:
            step["source_message_id"] = source_message_id
        self.steps.append(step)
        self._current_step_id = step_id
        self._filters[step_id] = VisibleTextFilter()
        self._enforce_total_limit()
        return {
            "type": "assistant_step_started",
            "message_id": self.message_id,
            "step": copy.deepcopy(step),
        }

    def _append_text(self, step_id: str, delta: str) -> str:
        step = self._step(step_id)
        if step is None or not delta:
            return ""
        existing = str(step.get("content", ""))
        available = max(0, MAX_STEP_TEXT - len(existing))
        visible = sanitize_text(delta[:available])
        newly_truncated = len(delta) > available and not step.get("truncated")
        if len(delta) > available:
            step["truncated"] = True
        step["content"] = existing + visible
        if visible:
            self._had_delta.add(step_id)
            # repr 对引号的选择可能随整段正文变化；两类引号都计入转义开销，
            # 保证估算只会早触发精确检查，不会越过总量上限。
            self._total_bytes += len(repr(visible).encode("utf-8")) - 2
            self._total_bytes += visible.count("'") + visible.count('"')
        if newly_truncated or "'" in visible or '"' in visible or self._total_bytes >= MAX_STEPS_BYTES:
            self._enforce_total_limit()
        return visible

    def project_text_delta(self, step_id: str, value: Any) -> dict[str, Any] | None:
        text = content_to_text(value)
        if not text:
            return None
        text_filter = self._filters.setdefault(step_id, VisibleTextFilter())
        delta = text_filter.feed(text)
        delta = self._append_text(step_id, delta)
        if not delta:
            return None
        return {
            "type": "assistant_text_delta",
            "message_id": self.message_id,
            "step_id": step_id,
            "delta": delta,
        }

    def _flush_filter(self, step_id: str) -> str:
        text_filter = self._filters.setdefault(step_id, VisibleTextFilter())
        return self._append_text(step_id, text_filter.finish())

    def _ensure_tool(
        self,
        step: dict[str, Any],
        *,
        call_id: str,
        name: str,
        batch_index: int,
        args: Any = None,
        args_preview: str | None = None,
        status: str = "running",
        started_at: int | None = None,
    ) -> dict[str, Any]:
        tools = step.setdefault("tool_calls", [])
        existing = next((item for item in tools if item.get("call_id") == call_id), None)
        if existing is None:
            existing = _tool_snapshot(
                call_id,
                name,
                batch_index,
                args=args,
                args_preview=args_preview,
                status=status,
                started_at=started_at,
            )
            tools.append(existing)
            tools.sort(key=lambda item: int(item.get("batch_index", 0)))
        else:
            existing["name"] = sanitize_text(name) or existing.get("name", "unknown")
            existing["batch_index"] = batch_index
            if args_preview is not None:
                existing["args_preview"] = _preview(args_preview)
            elif args is not None:
                existing["args_preview"] = _preview(_decode_tool_args(args))
            if status in TOOL_STATUSES and existing.get("status") not in {"completed", "failed"}:
                existing["status"] = status
        self._call_to_step[call_id] = str(step["id"])
        self._tool_step_id = str(step["id"])
        pending = self._pending_results.pop(call_id, None)
        if pending:
            self._update_tool_result(existing, pending)
        return existing

    def project_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        batch_index: int = 0,
        args: Any = None,
        args_preview: str | None = None,
        step_id: str | None = None,
        started_at: int | None = None,
    ) -> dict[str, Any] | None:
        step = self._step(step_id) if step_id else self._step_for_call(call_id)
        if step is None:
            self._bounded_pending(
                self._pending_calls,
                call_id,
                {
                    "call_id": call_id,
                    "name": name,
                    "batch_index": batch_index,
                    "args": args,
                    "args_preview": args_preview,
                    "started_at": started_at if started_at is not None else _now_ms(),
                },
            )
            return None
        tool = self._ensure_tool(
            step,
            call_id=call_id,
            name=name,
            batch_index=batch_index,
            args=args,
            args_preview=args_preview,
            started_at=started_at if started_at is not None else _now_ms(),
        )
        step["status"] = "running"
        self._enforce_total_limit()
        return {
            "type": "assistant_tool_call",
            "message_id": self.message_id,
            "step_id": str(step["id"]),
            "call": copy.deepcopy(tool),
        }

    def _update_tool_result(self, tool: dict[str, Any], result: dict[str, Any]) -> None:
        status = str(result.get("status", "unknown"))
        tool["status"] = status if status in TOOL_STATUSES else "unknown"
        if result.get("result_preview") is not None:
            tool["result_preview"] = _preview(str(result["result_preview"]))
        if result.get("error"):
            tool["error"] = sanitize_text(str(result["error"]))
        completed_at = result.get("completed_at")
        tool["completed_at"] = int(completed_at) if completed_at is not None else _now_ms()

    def project_tool_result(
        self,
        *,
        call_id: str,
        result: Any = None,
        status: str = "completed",
        error: str | None = None,
        step_id: str | None = None,
        completed_at: int | None = None,
    ) -> dict[str, Any] | None:
        step = self._step(step_id) if step_id else self._step_for_call(call_id)
        result_preview = _preview(result) if result is not None else None
        result_data = {
            "result_preview": result_preview,
            "status": status,
            "error": error,
            "completed_at": completed_at if completed_at is not None else _now_ms(),
        }
        if step is None:
            self._bounded_pending(self._pending_results, call_id, result_data)
            return None
        tool = next((item for item in step.get("tool_calls", []) if item.get("call_id") == call_id), None)
        if tool is None:
            pending = self._pending_calls.pop(call_id, None) or {}
            # 从没观测到调用事件时不补 started_at：宁可不显示耗时，也不编造开始时间。
            tool = self._ensure_tool(
                step,
                call_id=call_id,
                name=str(pending.get("name", "unknown")),
                batch_index=int(pending.get("batch_index", len(step.get("tool_calls", [])))),
                args=pending.get("args"),
                args_preview=pending.get("args_preview"),
                started_at=pending.get("started_at"),
            )
        self._update_tool_result(tool, result_data)
        # 工具状态变化后同步 step 状态，避免恢复执行后 step 永远停在 waiting。
        if step.get("status") != "streaming":
            step["status"] = _step_status_from_tools(
                step.get("tool_calls", []),
                str(step.get("status", "unknown")),
            )
        self._enforce_total_limit()
        return {
            "type": "assistant_tool_result",
            "message_id": self.message_id,
            "step_id": str(step["id"]),
            "call_id": call_id,
            "result": copy.deepcopy(tool),
        }

    def complete_step(
        self,
        step_id: str,
        *,
        content: Any = "",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        step = self._step(step_id)
        if step is None:
            return []
        full_text = _text(content)
        filtered_tail = self._flush_filter(step_id)
        if full_text:
            # 完整 AIMessage 是本轮文本的最终对账快照；流式 delta 只负责低延迟，
            # provider 在结束时补齐的正文不能因为前端已经收到部分 delta 而丢失。
            step["content"] = full_text[:MAX_STEP_TEXT]
            if len(full_text) > MAX_STEP_TEXT:
                step["truncated"] = True
        elif filtered_tail and step_id not in self._had_delta:
            self._append_text(step_id, filtered_tail)
        emitted_tools: list[dict[str, Any]] = []
        pending_calls = list(self._pending_calls.values())
        self._pending_calls.clear()
        for index, call in enumerate([*pending_calls, *(tool_calls or [])]):
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or call.get("call_id") or f"{step_id}:tool:{index}")
            event = self.project_tool_call(
                call_id=call_id,
                name=str(call.get("name") or "unknown"),
                batch_index=int(call.get("batch_index", index)),
                args=call.get("args"),
                step_id=step_id,
                started_at=call.get("started_at"),
            )
            if event is not None:
                emitted_tools.append(event)
                call_snapshot = event.get("call") or {}
                if call_snapshot.get("status") in {"completed", "failed", "waiting", "unknown"}:
                    emitted_tools.append(
                        {
                            "type": "assistant_tool_result",
                            "message_id": self.message_id,
                            "step_id": step_id,
                            "call_id": call_id,
                            "result": copy.deepcopy(call_snapshot),
                        }
                    )
        step["status"] = "running" if step.get("tool_calls") else "completed"
        self._enforce_total_limit()
        self._current_step_id = step_id
        return [
            *emitted_tools,
            {
                "type": "assistant_step_completed",
                "message_id": self.message_id,
                "step_id": step_id,
                "content": step.get("content", ""),
                "tool_calls": copy.deepcopy(step.get("tool_calls", [])),
                "status": step["status"],
            }
        ]

    def _enforce_total_limit(self) -> None:
        self._total_bytes = len(str(self.steps).encode("utf-8"))
        while self._total_bytes > MAX_STEPS_BYTES:
            candidates = [item for item in self.steps if item.get("content")]
            if not candidates:
                break
            largest = max(candidates, key=lambda item: len(str(item.get("content", ""))))
            content = str(largest.get("content", ""))
            keep = max(0, len(content) - max(1024, len(content) // 10))
            largest["content"] = content[:keep] + "\n…（执行轨迹已截断）"
            largest["truncated"] = True
            self._total_bytes = len(str(self.steps).encode("utf-8"))

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "assistant_step_started":
            step = copy.deepcopy(event.get("step") or {})
            if not step or any(item.get("id") == step.get("id") for item in self.steps):
                return
            self.steps.append(step)
            self._filters.setdefault(str(step.get("id")), VisibleTextFilter())
            self._current_step_id = str(step.get("id"))
            self._enforce_total_limit()
            return
        step_id = str(event.get("step_id", ""))
        step = self._step(step_id)
        if step is None:
            return
        if event_type == "assistant_text_delta":
            self._append_text(step_id, str(event.get("delta", "")))
        elif event_type == "assistant_tool_call":
            call = event.get("call") or {}
            self._ensure_tool(
                step,
                call_id=str(call.get("call_id", "unknown")),
                name=str(call.get("name", "unknown")),
                batch_index=int(call.get("batch_index", len(step.get("tool_calls", [])))),
                args_preview=call.get("args_preview"),
                status=str(call.get("status", "running")),
                started_at=call.get("started_at"),
            )
        elif event_type == "assistant_tool_result":
            result = event.get("result") or {}
            call_id = str(event.get("call_id", result.get("call_id", "")))
            tool = next((item for item in step.get("tool_calls", []) if item.get("call_id") == call_id), None)
            if tool is not None:
                self._update_tool_result(tool, result)
                if step.get("status") != "streaming":
                    step["status"] = _step_status_from_tools(
                        step.get("tool_calls", []),
                        str(step.get("status", "unknown")),
                    )
        elif event_type == "assistant_step_completed":
            step["content"] = sanitize_text(str(event.get("content", "")))[:MAX_STEP_TEXT]
            step["tool_calls"] = copy.deepcopy(event.get("tool_calls") or [])
            step["status"] = event.get("status", step.get("status", "completed"))
            for tool in step["tool_calls"]:
                self._call_to_step[str(tool.get("call_id", ""))] = step_id
        if event_type != "assistant_text_delta":
            self._enforce_total_limit()

    def terminal_snapshot(self, *, status: str, final_content: str | None = None) -> list[dict[str, Any]]:
        if status == "completed":
            content = _text(final_content or "")
            candidate = None
            for step in reversed(self.steps):
                if not step.get("tool_calls") and (step.get("content") or content):
                    candidate = step
                    break
            if candidate is None and content:
                self.start_step()
                candidate = self.steps[-1]
            if candidate is not None:
                if content:
                    candidate["content"] = content[:MAX_STEP_TEXT]
                candidate["is_final"] = True
            for step in self.steps:
                if step is not candidate:
                    step["is_final"] = False
                if step.get("status") in {"streaming", "running", "waiting"}:
                    step["status"] = "completed"
        elif status == "interrupted":
            for step in self.steps:
                if step.get("status") in {"streaming", "running"}:
                    step["status"] = "waiting"
                for tool in step.get("tool_calls", []) or []:
                    if tool.get("status") == "running":
                        tool["status"] = "waiting"
        else:
            for step in self.steps:
                if step.get("status") in {"streaming", "running", "waiting"}:
                    step["status"] = "failed" if status == "failed" else "unknown"
                for tool in step.get("tool_calls", []) or []:
                    if tool.get("status") in {"running", "waiting"}:
                        tool["status"] = "unknown"
        self._enforce_total_limit()
        return _copy_steps(self.steps)

    def visible_content(self) -> str:
        for step in reversed(self.steps):
            if step.get("content"):
                return str(step["content"])
        return ""


def events_from_snapshot(message_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把历史 steps 展开成当前版本 SSE 事件，供幂等回执重放。"""

    events: list[dict[str, Any]] = []
    for step in sorted(steps, key=lambda item: int(item.get("ordinal", 0))):
        events.append({"type": "assistant_step_started", "message_id": message_id, "step": copy.deepcopy(step)})
        if step.get("content"):
            events.append({
                "type": "assistant_text_delta",
                "message_id": message_id,
                "step_id": step["id"],
                "delta": step["content"],
            })
        for tool in sorted(step.get("tool_calls", []) or [], key=lambda item: int(item.get("batch_index", 0))):
            events.append({
                "type": "assistant_tool_call",
                "message_id": message_id,
                "step_id": step["id"],
                "call": copy.deepcopy(tool),
            })
            if tool.get("status") in {"completed", "failed", "waiting", "unknown"}:
                events.append({
                    "type": "assistant_tool_result",
                    "message_id": message_id,
                    "step_id": step["id"],
                    "call_id": tool["call_id"],
                    "result": copy.deepcopy(tool),
                })
        events.append({
            "type": "assistant_step_completed",
            "message_id": message_id,
            "step_id": step["id"],
            "content": step.get("content", ""),
            "tool_calls": copy.deepcopy(step.get("tool_calls", [])),
            "status": step.get("status", "completed"),
        })
    return events


AssistantStepProjector = AssistantStepAccumulator

__all__ = ["AssistantStepAccumulator", "AssistantStepProjector", "events_from_snapshot"]
