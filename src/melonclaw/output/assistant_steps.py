"""根 Agent assistant steps 的纯内存投影与终态归约。

一个 step 对应一次根 Agent 的 AIMessage。这个模块只处理展示层需要的安全快照，
不读取数据库，也不依赖 Web 层；事件适配器生成增量，执行服务复用同一快照落库。
"""

from __future__ import annotations

import copy
from collections import OrderedDict
from time import time
from typing import Any

from melonclaw.output.content import answer_text, content_to_text, display_blocks
from melonclaw.output.formatting import _decode_tool_args, _preview, sanitize_text

STEP_STATUSES = frozenset({"streaming", "running", "completed", "failed", "waiting", "unknown"})
TOOL_STATUSES = frozenset({"queued", "running", "completed", "failed", "waiting", "unknown"})
MAX_STEP_TEXT = 120 * 1024
MAX_STEPS_BYTES = 512 * 1024
MAX_PENDING_CALLS = 128
TRUNCATION_MARKER = "\n…（执行轨迹已截断）"


def _now_ms() -> int:
    """epoch 毫秒只记录观测时间；运行耗时优先使用单调时钟 duration_ms。"""

    return int(time() * 1000)


def _copy_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return copy.deepcopy(steps or [])


def _text(value: Any) -> str:
    return sanitize_text(content_to_text(value))


def _step_answer_text(step: dict[str, Any]) -> str:
    content = answer_text(step.get("content_blocks", step.get("content", "")))
    return "" if step.get("truncated") and content.strip() == TRUNCATION_MARKER.strip() else content


def _step_display_text(step: dict[str, Any]) -> str:
    if "content_blocks" in step:
        return "".join(block["text"] for block in step["content_blocks"])
    return str(step.get("content", ""))


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
    if "queued" in statuses:
        return "running"
    for status in ("running", "waiting", "failed", "unknown"):
        if status in statuses:
            return status
    return "completed"


class AssistantStepAccumulator:
    """维护一条 assistant 消息内的有序 steps。

    ``project_*`` 方法用于输出 SSE，同时更新本地快照；``apply_event`` 用于执行
    服务消费已经投影出的事件。两条路径都只保留脱敏和限长后的内容。
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
                    if tool.get("status") in {"queued", "running", "waiting"}:
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
            # repr 对引号的选择可能随整段正文变化；两类引号都计入转义开销，
            # 保证估算只会早触发精确检查，不会越过总量上限。
            self._total_bytes += len(repr(visible).encode("utf-8")) - 2
            self._total_bytes += visible.count("'") + visible.count('"')
        if newly_truncated or "'" in visible or '"' in visible or self._total_bytes >= MAX_STEPS_BYTES:
            self._enforce_total_limit()
        return visible

    def project_text_delta(self, step_id: str, value: Any, *, kind: str = "text") -> dict[str, Any] | None:
        text = content_to_text(value)
        if not text:
            return None
        step = self._step(step_id)
        if step is None:
            return None
        before = str(step.get("content", ""))
        delta = self._append_text(step_id, text)
        if not delta:
            return None
        if kind == "reasoning" or "content_blocks" in step:
            blocks = step.setdefault("content_blocks", [{"type": "text", "text": before}] if before else [])
            if blocks and blocks[-1]["type"] == kind:
                blocks[-1]["text"] += delta
            else:
                blocks.append({"type": kind, "text": delta})
            self._total_bytes += len(delta.encode("utf-8")) + 32
            if self._total_bytes >= MAX_STEPS_BYTES:
                self._enforce_total_limit()
        return {
            "type": "assistant_text_delta",
            "message_id": self.message_id,
            "step_id": step_id,
            "delta": delta,
            "content_kind": kind,
        }

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
            if status == "queued":
                existing["batch_index"] = batch_index
            if args_preview is not None:
                existing["args_preview"] = _preview(args_preview)
            elif args is not None:
                existing["args_preview"] = _preview(_decode_tool_args(args))
            if status in TOOL_STATUSES and existing.get("status") not in {"completed", "failed"}:
                if status != "queued" or existing.get("status") == "queued":
                    existing["status"] = status
            if started_at is not None and "started_at" not in existing:
                existing["started_at"] = int(started_at)
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
        batch_index: int | None = None,
        args: Any = None,
        args_preview: str | None = None,
        step_id: str | None = None,
        started_at: int | None = None,
        status: str = "running",
    ) -> dict[str, Any] | None:
        step = self._step(step_id) if step_id else self._step_for_call(call_id)
        if step is None:
            self._bounded_pending(
                self._pending_calls,
                call_id,
                {
                    "call_id": call_id,
                    "name": name,
                    "batch_index": batch_index or 0,
                    "status": status,
                    "args": args,
                    "args_preview": args_preview,
                    "started_at": (started_at if started_at is not None else _now_ms()) if status == "running" else None,
                },
            )
            return None
        tool = self._ensure_tool(
            step,
            call_id=call_id,
            name=name,
            batch_index=batch_index if batch_index is not None else len(step.get("tool_calls", [])),
            args=args,
            args_preview=args_preview,
            status=status,
            started_at=(started_at if started_at is not None else _now_ms()) if status == "running" else None,
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
        if result.get("duration_ms") is not None:
            tool["duration_ms"] = max(0, int(result["duration_ms"]))

    def project_tool_result(
        self,
        *,
        call_id: str,
        result: Any = None,
        status: str = "completed",
        error: str | None = None,
        step_id: str | None = None,
        completed_at: int | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any] | None:
        step = self._step(step_id) if step_id else self._step_for_call(call_id)
        result_preview = _preview(result) if result is not None else None
        result_data = {
            "result_preview": result_preview,
            "status": status,
            "error": error,
            "completed_at": completed_at if completed_at is not None else _now_ms(),
        }
        if duration_ms is not None:
            result_data["duration_ms"] = duration_ms
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
        blocks = display_blocks(content)
        if any(block["type"] == "reasoning" for block in blocks):
            available = MAX_STEP_TEXT
            bounded = []
            for block in blocks:
                value = sanitize_text(block["text"][:available])
                if value:
                    bounded.append({"type": block["type"], "text": value})
                    available -= len(value)
            step["content_blocks"] = bounded
        if full_text:
            # 完整 AIMessage 是本轮文本的最终对账快照；流式 delta 只负责低延迟，
            # provider 在结束时补齐的正文不能因为前端已经收到部分 delta 而丢失。
            step["content"] = full_text[:MAX_STEP_TEXT]
            if len(full_text) > MAX_STEP_TEXT:
                step["truncated"] = True
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
                status=call.get("status", "queued"),
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
        step["status"] = _step_status_from_tools(step.get("tool_calls", []), "completed")
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
                "content_blocks": copy.deepcopy(step.get("content_blocks")),
            }
        ]

    def _enforce_total_limit(self) -> None:
        self._total_bytes = len(str(self.steps).encode("utf-8"))
        while self._total_bytes > MAX_STEPS_BYTES:
            candidates = [
                item for item in self.steps
                if _step_display_text(item) not in {"", TRUNCATION_MARKER}
            ]
            if not candidates:
                break
            largest = max(candidates, key=lambda item: len(_step_display_text(item)))
            content = _step_display_text(largest)
            if largest.get("truncated") and content.endswith(TRUNCATION_MARKER):
                content = content[:-len(TRUNCATION_MARKER)]
            # 短步骤每次至多减半，避免只超出少量字节却整段删除。
            # 每轮至少删除一个原字符；标记不再次参与截断，保证循环终止。
            remove = min(max(1024, len(content) // 10), max(1, len(content) // 2))
            keep = max(0, len(content) - remove)
            largest["content"] = content[:keep] + TRUNCATION_MARKER
            if "content_blocks" in largest:
                bounded = []
                remaining = keep
                for block in largest["content_blocks"]:
                    if remaining <= 0:
                        break
                    value = block["text"][:remaining]
                    bounded.append({"type": block["type"], "text": value})
                    remaining -= len(value)
                if bounded:
                    bounded[-1]["text"] += TRUNCATION_MARKER
                else:
                    kind = largest["content_blocks"][0]["type"] if largest["content_blocks"] else "text"
                    bounded.append({"type": kind, "text": TRUNCATION_MARKER})
                largest["content_blocks"] = bounded
            largest["truncated"] = True
            self._total_bytes = len(str(self.steps).encode("utf-8"))

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "assistant_step_started":
            step = copy.deepcopy(event.get("step") or {})
            if not step or any(item.get("id") == step.get("id") for item in self.steps):
                return
            self.steps.append(step)
            self._current_step_id = str(step.get("id"))
            self._enforce_total_limit()
            return
        step_id = str(event.get("step_id", ""))
        step = self._step(step_id)
        if step is None:
            return
        if event_type == "assistant_text_delta":
            self.project_text_delta(step_id, str(event.get("delta", "")), kind=event.get("content_kind", "text"))
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
            if event.get("content_blocks") is not None:
                step["content_blocks"] = copy.deepcopy(event["content_blocks"])
            for tool in step["tool_calls"]:
                self._call_to_step[str(tool.get("call_id", ""))] = step_id
        if event_type != "assistant_text_delta":
            self._enforce_total_limit()

    def terminal_snapshot(self, *, status: str, final_content: str | None = None) -> list[dict[str, Any]]:
        if status == "completed":
            content = _text(final_content or "")
            candidate = None
            for step in reversed(self.steps):
                if not step.get("tool_calls") and _step_answer_text(step).strip():
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
                    if tool.get("status") in {"queued", "running"}:
                        tool["status"] = "waiting"
        else:
            for step in self.steps:
                if step.get("status") in {"streaming", "running", "waiting"}:
                    step["status"] = "failed" if status == "failed" else "unknown"
                for tool in step.get("tool_calls", []) or []:
                    if tool.get("status") in {"queued", "running", "waiting"}:
                        tool["status"] = "unknown"
        self._enforce_total_limit()
        return _copy_steps(self.steps)

    def visible_content(self) -> str:
        for step in reversed(self.steps):
            if not step.get("tool_calls"):
                content = _step_answer_text(step)
                if content.strip():
                    return content
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
            "content_blocks": copy.deepcopy(step.get("content_blocks")),
        })
    return events


AssistantStepProjector = AssistantStepAccumulator

__all__ = ["AssistantStepAccumulator", "AssistantStepProjector", "events_from_snapshot"]
