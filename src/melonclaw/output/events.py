"""把 Deep Agents 流转换成 Web 可消费的结构化事件。

Deep Agents 的 v3 流协议把协调 Agent、工具调用和命名子 Agent 拆成了
可独立消费的投影。这里把这些投影收敛成稳定的应用事件，Web 层不需要依赖
LangChain 的前端 SDK，也不需要理解 ``AsyncGraphRunStream`` 的内部结构。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import Any

from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.output.content import content_to_text
from melonclaw.output.formatting import (
    _decode_tool_args,
    _preview,
    sanitize_text,
)
from melonclaw.output.visible_text import VisibleTextFilter, visible_text

DISPLAY_EVENT_TYPES = frozenset(
    {
        "subagent_started",
        "subagent_text",
        "subagent_tool_call",
        "subagent_tool_result",
        "subagent_completed",
        "subagent_failed",
    }
)


def _scope_id(namespace: tuple[str, ...]) -> str:
    """为一个 v3 命名空间生成稳定、可安全放进 DOM 的 ID。"""

    return "subagent:" + "/".join(namespace)


def _scope_fields(scope: dict[str, Any]) -> dict[str, Any]:
    """返回所有子 Agent 事件都共用的关联字段。"""

    namespace = tuple(scope.get("namespace", ()))
    return {
        "subagent_id": scope["subagent_id"],
        "subagent_name": scope.get("name") or "general-purpose",
        "namespace": list(namespace),
        "parent_call_id": scope.get("parent_call_id"),
        "parent_subagent_id": _scope_id(namespace[:-1]) if len(namespace) > 1 else None,
    }


def _parent_call_id(handle: Any) -> str | None:
    cause = getattr(handle, "cause", None)
    if isinstance(cause, dict):
        value = cause.get("tool_call_id")
        return str(value) if value else None
    value = getattr(cause, "tool_call_id", None)
    return str(value) if value else None


def _is_tool_selector_message(message_stream: Any) -> bool:
    """隐藏内部工具选择器消息，读取当前 v3 的启动元数据。"""

    candidates = [
        getattr(message_stream, "metadata", None),
        getattr(message_stream, "_start_metadata", None),
    ]
    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        if metadata.get("tool_selector") or "tool-selector" in (metadata.get("tags") or []):
            return True
    return False


def _tool_output_text(value: Any) -> str:
    """从 ToolCallStream 的 output/Command 中提取适合浏览器展示的文本。"""

    if value is None:
        return "<无文本输出>"
    update = getattr(value, "update", None)
    if isinstance(update, dict):
        return _tool_output_text(update)
    if isinstance(value, dict):
        for key in ("messages", "output", "content", "result"):
            if key in value:
                text = _tool_output_text(value[key])
                if text != "<无文本输出>":
                    return text
        return _preview(value)
    if isinstance(value, (list, tuple)):
        parts = [_tool_output_text(item) for item in value]
        parts = [part for part in parts if part != "<无文本输出>"]
        return "\n".join(parts) if parts else "<无文本输出>"
    content = getattr(value, "content", None)
    if content is not None:
        text = content_to_text(content)
        return _preview(text) if text else "<无文本输出>"
    text = content_to_text(value)
    return _preview(text) if text else _preview(value)


async def _consume_messages(
    scope_stream: Any,
    scope: dict[str, Any] | None,
    output: asyncio.Queue[dict[str, Any] | BaseException | object],
    projector: AssistantStepAccumulator | None = None,
) -> None:
    """消费一个命名空间中的模型文本投影。"""

    async for message_stream in scope_stream.messages:
        if _is_tool_selector_message(message_stream):
            if scope is None:
                await output.put({"type": "run_phase", "phase": "selecting_tools"})
            # 仍然把该 message 的生产者排空，避免阻塞 v3 的共享 pump。
            async for _ in message_stream.text:
                pass
            if scope is None:
                await output.put({"type": "run_phase", "phase": "thinking"})
            continue

        if scope is None:
            await output.put({"type": "run_phase", "phase": "thinking"})
            if projector is None:
                text_filter = VisibleTextFilter()
                async for delta in message_stream.text:
                    text = text_filter.feed(content_to_text(delta))
                    if text:
                        await output.put({"type": "text", "text": sanitize_text(text)})
                tail = text_filter.finish()
                if tail:
                    await output.put({"type": "text", "text": sanitize_text(tail)})
                continue
            source_message_id = str(getattr(message_stream, "id", None) or "") or None
            started = projector.start_step(source_message_id=source_message_id)
            await output.put(started)
            step_id = str(started["step"]["id"])
        else:
            step_id = ""
        chunks: list[str] = []
        text_filter = VisibleTextFilter() if scope is not None else None
        async for delta in message_stream.text:
            text = content_to_text(delta)
            if not text:
                continue
            chunks.append(text)
            if scope is None:
                event = projector.project_text_delta(step_id, text)
                if event is not None:
                    await output.put(event)
            else:
                text = text_filter.feed(text)
                if not text:
                    continue
                await output.put(
                    {
                        "type": "subagent_text",
                        **_scope_fields(scope),
                        "text": sanitize_text(text),
                    }
                )

        if scope is not None:
            tail = text_filter.finish()
            if tail:
                await output.put(
                    {"type": "subagent_text", **_scope_fields(scope), "text": sanitize_text(tail)}
                )

        # 非流式 provider 可能只在最终 message 中提供正文；不要让它在前端
        # 看起来像“子 Agent 没有输出”。仅在没有任何 delta 时补发一次。
        try:
            raw_output = message_stream.output
            message = await raw_output if inspect.isawaitable(raw_output) else raw_output
        except Exception:  # noqa: BLE001 - provider output is optional fallback
            message = None
        if scope is None:
            raw_tool_calls = getattr(message, "tool_calls", None) if message is not None else None
            full_content = getattr(message, "content", "") if message is not None else ""
            if isinstance(message, dict):
                raw_tool_calls = message.get("tool_calls")
                full_content = message.get("content", "")
            tool_calls = [
                call for call in (raw_tool_calls or []) if isinstance(call, dict)
            ]
            for event in projector.complete_step(
                step_id,
                content=full_content,
                tool_calls=tool_calls,
            ):
                await output.put(event)
        elif not chunks:
            text = visible_text(content_to_text(getattr(message, "content", "")))
            if text:
                await output.put(
                    {
                        "type": "subagent_text",
                        **_scope_fields(scope),
                        "text": sanitize_text(text),
                    }
                )


async def _consume_tool_calls(
    scope_stream: Any,
    scope: dict[str, Any] | None,
    output: asyncio.Queue[dict[str, Any] | BaseException | object],
    projector: AssistantStepAccumulator | None = None,
) -> None:
    """消费一个命名空间中的工具调用生命周期。"""

    async for call in scope_stream.tool_calls:
        raw_call_id = getattr(call, "tool_call_id", None) or "unknown"
        call_id = str(raw_call_id)
        name = sanitize_text(str(getattr(call, "tool_name", None) or "unknown"))
        if scope is None:
            if projector is None:
                raise RuntimeError("根 Agent 工具投影器未初始化。")
            args = getattr(call, "input", None)
            call_event = projector.project_tool_call(
                call_id=call_id,
                name=name,
                batch_index=int(getattr(call, "index", 0) or 0),
                args=args,
            )
            if call_event is not None:
                await output.put(call_event)
            try:
                async for _ in call.output_deltas:
                    pass
            except Exception as exc:  # noqa: BLE001 - 结果事件仍需告知前端
                result_event = projector.project_tool_result(
                    call_id=call_id,
                    result=str(exc),
                    status="failed",
                    error=str(exc),
                )
            else:
                result_event = projector.project_tool_result(
                    call_id=call_id,
                    result=call.error or _tool_output_text(getattr(call, "output", None)),
                    status="failed" if call.error else "completed",
                    error=str(call.error) if call.error else None,
                )
            if result_event is not None:
                await output.put(result_event)
            continue
        call_key = f"id:{call_id}" if scope is None else f"{scope['subagent_id']}:id:{call_id}"
        fields = {} if scope is None else _scope_fields(scope)
        event_type = "tool_call" if scope is None else "subagent_tool_call"
        result_type = "tool_result" if scope is None else "subagent_tool_result"
        await output.put(
            {
                "type": event_type,
                "call_key": call_key,
                "call_id": call_id,
                "name": name,
                "status": "started",
                **fields,
            }
        )
        args = getattr(call, "input", None)
        if args is not None:
            await output.put(
                {
                    "type": event_type,
                    "call_key": call_key,
                    "call_id": call_id,
                    "name": name,
                    "status": "args",
                    "args": _preview(_decode_tool_args(args)),
                    **fields,
                }
            )
        try:
            # output_deltas 会在工具完成时关闭；即使没有增量，也必须排空它，
            # 否则 ToolCallStream 的 output 还没有被填充。
            async for _ in call.output_deltas:
                pass
        except Exception as exc:  # noqa: BLE001 - 结果事件仍需告知前端
            await output.put(
                {
                    "type": result_type,
                    "call_key": call_key,
                    "call_id": call_id,
                    "name": name,
                    "content": sanitize_text(str(exc)),
                    "status": "failed",
                    **fields,
                }
            )
            continue
        content = call.error or _tool_output_text(getattr(call, "output", None))
        await output.put(
            {
                "type": result_type,
                "call_key": call_key,
                "call_id": call_id,
                "name": name,
                "content": sanitize_text(str(content)),
                "status": "failed" if call.error else "completed",
                **fields,
            }
        )


async def _consume_scope(
    handle: Any,
    scope: dict[str, Any],
    output: asyncio.Queue[dict[str, Any] | BaseException | object],
) -> None:
    """递归消费一个子 Agent 及其可能继续委派的子 Agent。"""

    tasks = [
        asyncio.create_task(_consume_messages(handle, scope, output)),
        asyncio.create_task(_consume_tool_calls(handle, scope, output)),
        asyncio.create_task(_consume_subagents(handle, scope, output)),
    ]
    errors: list[BaseException] = []
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        status = "failed" if errors or getattr(handle, "status", None) == "failed" else "completed"
        event: dict[str, Any] = {
            "type": "subagent_failed" if status == "failed" else "subagent_completed",
            **_scope_fields(scope),
            "status": status,
        }
        error = getattr(handle, "error", None) or (str(errors[0]) if errors else None)
        if error:
            event["error"] = sanitize_text(str(error))
        await output.put(event)
        if errors:
            raise errors[0]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _consume_subagents(
    scope_stream: Any,
    parent_scope: dict[str, Any] | None,
    output: asyncio.Queue[dict[str, Any] | BaseException | object],
) -> None:
    """发现并递归消费当前命名空间直接启动的子 Agent。"""

    child_tasks: list[asyncio.Task[None]] = []
    subagents = getattr(scope_stream, "subagents", None)
    if subagents is None:
        return
    async for handle in subagents:
        namespace = tuple(getattr(handle, "path", ()) or ())
        if not namespace:
            continue
        scope = {
            "subagent_id": _scope_id(namespace),
            "name": getattr(handle, "name", None) or getattr(handle, "graph_name", None),
            "namespace": namespace,
            "parent_call_id": _parent_call_id(handle),
        }
        await output.put(
            {
                "type": "subagent_started",
                **_scope_fields(scope),
                "status": "running",
            }
        )
        child_tasks.append(asyncio.create_task(_consume_scope(handle, scope, output)))
    if child_tasks:
        results = await asyncio.gather(*child_tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise errors[0]


async def _iter_v3_research_events(
    agent: Any,
    agent_input: Any,
    config: dict[str, Any],
    *,
    context: Any | None = None,
    assistant_message_id: str | None = None,
    run_id: str | None = None,
    assistant_steps: list[dict[str, Any]] | None = None,
    projector: AssistantStepAccumulator | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """使用 Deep Agents/LangGraph v3 投影并发消费根 Agent 与子 Agent。"""

    stream_kwargs: dict[str, Any] = {"config": config, "version": "v3"}
    if context is not None:
        stream_kwargs["context"] = context
    stream_result = agent.astream_events(agent_input, **stream_kwargs)
    stream = await stream_result if inspect.isawaitable(stream_result) else stream_result

    end_marker = object()
    queue: asyncio.Queue[dict[str, Any] | BaseException | object] = asyncio.Queue()
    if projector is None:
        projector = AssistantStepAccumulator(
            message_id=assistant_message_id or "assistant",
            run_id=run_id or assistant_message_id or "run",
            steps=assistant_steps,
        )
    root_tasks = [
        asyncio.create_task(_consume_messages(stream, None, queue, projector)),
        asyncio.create_task(_consume_tool_calls(stream, None, queue, projector)),
        asyncio.create_task(_consume_subagents(stream, None, queue)),
    ]

    async def supervise() -> None:
        try:
            results = await asyncio.gather(*root_tasks, return_exceptions=True)
            errors = [result for result in results if isinstance(result, BaseException)]
            if errors:
                await queue.put(errors[0])
        finally:
            for task in root_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*root_tasks, return_exceptions=True)
            await queue.put(end_marker)

    supervisor = asyncio.create_task(supervise())
    try:
        while True:
            item = await queue.get()
            if item is end_marker:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
        await supervisor
    finally:
        if not supervisor.done():
            supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
        abort = getattr(stream, "abort", None)
        if abort is not None:
            await abort()


async def iter_research_events(
    agent: Any,
    agent_input: Any,
    config: dict[str, Any],
    *,
    context: Any | None = None,
    assistant_message_id: str | None = None,
    run_id: str | None = None,
    assistant_steps: list[dict[str, Any]] | None = None,
    projector: AssistantStepAccumulator | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """将模型消息、工具调用和工具结果转换为安全的结构化事件。

    事件只包含已经经过长度限制和敏感信息脱敏的展示文本。这样 Web 层不需要
    理解 LangChain 的具体消息类型，也不会把工具参数原样发给浏览器。
    """

    # 当前 Deep Agents 版本统一使用 v3 投影；不再维护旧的 v2 消息流适配器。
    async for event in _iter_v3_research_events(
        agent,
        agent_input,
        config,
        context=context,
        assistant_message_id=assistant_message_id,
        run_id=run_id,
        assistant_steps=assistant_steps,
        projector=projector,
    ):
        yield event
