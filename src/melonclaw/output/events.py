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

from melonclaw.output.content import content_to_text
from melonclaw.output.formatting import (
    _call_key,
    _decode_tool_args,
    _preview,
    sanitize_text,
)
from melonclaw.output.visible_text import VisibleTextFilter, visible_text

DISPLAY_EVENT_TYPES = frozenset(
    {
        "tool_call",
        "tool_result",
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
    """隐藏内部工具选择器的消息，兼容 v3 当前的私有启动元数据。"""

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
        chunks: list[str] = []
        text_filter = VisibleTextFilter()
        async for delta in message_stream.text:
            text = content_to_text(delta)
            if not text:
                continue
            chunks.append(text)
            text = text_filter.feed(text)
            if not text:
                continue
            if scope is None:
                await output.put({"type": "text", "text": sanitize_text(text)})
            else:
                await output.put(
                    {
                        "type": "subagent_text",
                        **_scope_fields(scope),
                        "text": sanitize_text(text),
                    }
                )

        tail = text_filter.finish()
        if tail:
            await output.put(
                {"type": "text", "text": sanitize_text(tail)} if scope is None else
                {"type": "subagent_text", **_scope_fields(scope), "text": sanitize_text(tail)}
            )

        # 非流式 provider 可能只在最终 message 中提供正文；不要让它在前端
        # 看起来像“子 Agent 没有输出”。仅在没有任何 delta 时补发一次。
        if not chunks:
            try:
                message = await message_stream.output
            except Exception:  # noqa: BLE001 - provider output is optional fallback
                message = None
            text = visible_text(content_to_text(getattr(message, "content", "")))
            if text:
                if scope is None:
                    await output.put({"type": "text", "text": sanitize_text(text)})
                else:
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
) -> None:
    """消费一个命名空间中的工具调用生命周期。"""

    async for call in scope_stream.tool_calls:
        raw_call_id = getattr(call, "tool_call_id", None) or "unknown"
        call_id = str(raw_call_id)
        name = sanitize_text(str(getattr(call, "tool_name", None) or "unknown"))
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
) -> AsyncIterator[dict[str, Any]]:
    """使用 Deep Agents/LangGraph v3 投影并发消费根 Agent 与子 Agent。"""

    stream_kwargs: dict[str, Any] = {"config": config, "version": "v3"}
    if context is not None:
        stream_kwargs["context"] = context
    stream_result = agent.astream_events(agent_input, **stream_kwargs)
    stream = await stream_result if inspect.isawaitable(stream_result) else stream_result

    end_marker = object()
    queue: asyncio.Queue[dict[str, Any] | BaseException | object] = asyncio.Queue()
    root_tasks = [
        asyncio.create_task(_consume_messages(stream, None, queue)),
        asyncio.create_task(_consume_tool_calls(stream, None, queue)),
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
) -> AsyncIterator[dict[str, Any]]:
    """将模型消息、工具调用和工具结果转换为安全的结构化事件。

    事件只包含已经经过长度限制和敏感信息脱敏的展示文本。这样 Web 层不需要
    理解 LangChain 的具体消息类型，也不会把工具参数原样发给浏览器。
    """

    # v3 是当前 Deep Agents 文档推荐的产品级事件接口；旧适配器保留给
    # 没有 astream_events(version=...) 的测试替身和旧 LangGraph 版本。
    astream_events = getattr(agent, "astream_events", None)
    if astream_events is not None:
        try:
            parameters = inspect.signature(astream_events).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "version" in parameters:
            async for event in _iter_v3_research_events(
                agent,
                agent_input,
                config,
                context=context,
            ):
                yield event
            return

    async for event in _iter_legacy_research_events(
        agent,
        agent_input,
        config,
        context=context,
    ):
        yield event


async def _iter_legacy_research_events(
    agent: Any,
    agent_input: Any,
    config: dict[str, Any],
    *,
    context: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """v2/旧版本兼容实现；保持原有事件契约。"""

    pending_calls: dict[str, dict[str, Any]] = {}
    call_ids: dict[str, str] = {}
    index_keys: dict[int, str] = {}
    started_calls: set[str] = set()
    emitted_args: set[str] = set()
    text_filters: dict[str, VisibleTextFilter] = {}
    last_phase_message: tuple[str, str] | None = None

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "messages",
    }
    if context is not None:
        stream_kwargs["context"] = context

    async for message_chunk, metadata in agent.astream(agent_input, **stream_kwargs):
        message_type = getattr(message_chunk, "type", "")
        message_id = str(getattr(message_chunk, "id", None) or metadata.get("langgraph_node"))
        if (
            metadata.get("tool_selector")
            or "tool-selector" in metadata.get("tags", [])
        ):
            phase_message = (message_id, "selecting_tools")
            if phase_message != last_phase_message:
                yield {"type": "run_phase", "phase": "selecting_tools"}
                last_phase_message = phase_message
            continue

        if message_type == "tool":
            tool_call_id = getattr(message_chunk, "tool_call_id", None)
            key = call_ids.get(tool_call_id) if tool_call_id else None
            if key is None and pending_calls:
                key = next(iter(pending_calls))
            if key is None:
                key = f"result:{tool_call_id or getattr(message_chunk, 'name', 'unknown')}"

            pending = pending_calls.setdefault(key, {})
            name = pending.get("name") or getattr(message_chunk, "name", None) or "unknown"
            args = pending.get("args")
            if args in (None, {}) and pending.get("args_text"):
                args = pending["args_text"]
            if key not in started_calls:
                yield {
                    "type": "tool_call",
                    "call_key": key,
                    "name": sanitize_text(str(name)),
                    "status": "started",
                }
                started_calls.add(key)
            if key not in emitted_args:
                yield {
                    "type": "tool_call",
                    "call_key": key,
                    "name": sanitize_text(str(name)),
                    "status": "args",
                    "args": _preview(_decode_tool_args(args)),
                }
                emitted_args.add(key)

            raw_content = getattr(message_chunk, "content", "")
            result = content_to_text(raw_content)
            if not result:
                result = raw_content if isinstance(raw_content, str) else raw_content or "<无文本输出>"
            yield {
                "type": "tool_result",
                "call_key": key,
                "name": sanitize_text(str(name)),
                "content": _preview(result),
            }
            continue

        if message_type not in {"ai", "AIMessageChunk"}:
            continue

        # 某些模型先发送 tool_call_chunks，再发送完整 tool_calls；先缓存分片，
        # 等工具真正执行时再把完整参数作为展示事件发出。
        for index, chunk in enumerate(getattr(message_chunk, "tool_call_chunks", []) or []):
            if not isinstance(chunk, dict):
                continue
            key = _call_key(chunk, index, index_keys)
            pending = pending_calls.setdefault(key, {})
            if chunk.get("id"):
                call_ids[str(chunk["id"])] = key
            if chunk.get("name"):
                pending["name"] = chunk["name"]
            raw_args = chunk.get("args")
            if isinstance(raw_args, str):
                pending["args_text"] = pending.get("args_text", "") + raw_args
            elif raw_args is not None:
                pending["args"] = raw_args
            if pending.get("name") and key not in started_calls:
                yield {
                    "type": "tool_call",
                    "call_key": key,
                    "name": sanitize_text(str(pending["name"])),
                    "status": "started",
                }
                started_calls.add(key)

        for index, call in enumerate(getattr(message_chunk, "tool_calls", []) or []):
            if not isinstance(call, dict):
                continue
            key = _call_key(call, index, index_keys)
            pending = pending_calls.setdefault(key, {})
            if call.get("id"):
                call_ids[str(call["id"])] = key
            if call.get("name"):
                pending["name"] = call["name"]
            if "args" in call:
                call_args = call["args"]
                if call_args or not pending.get("args_text"):
                    pending["args"] = call_args
            if pending.get("name") and key not in started_calls:
                yield {
                    "type": "tool_call",
                    "call_key": key,
                    "name": sanitize_text(str(pending["name"])),
                    "status": "started",
                }
                started_calls.add(key)

        if metadata.get("langgraph_node") not in {"model", "agent"}:
            continue
        phase_message = (message_id, "thinking")
        if phase_message != last_phase_message:
            yield {"type": "run_phase", "phase": "thinking"}
            last_phase_message = phase_message
        text_filter = text_filters.setdefault(message_id, VisibleTextFilter())
        text = text_filter.feed(content_to_text(getattr(message_chunk, "content", "")))
        if text:
            yield {"type": "text", "text": sanitize_text(text)}

    for text_filter in text_filters.values():
        tail = text_filter.finish()
        if tail:
            yield {"type": "text", "text": sanitize_text(tail)}

    # 兼容只发出参数分片、但没有 ToolMessage 的模型或失败路径。
    for key, pending in pending_calls.items():
        if key in emitted_args:
            continue
        args = pending.get("args", pending.get("args_text"))
        if args in (None, {}) and pending.get("args_text"):
            args = pending["args_text"]
        if args is None:
            continue
        yield {
            "type": "tool_call",
            "call_key": key,
            "name": sanitize_text(str(pending.get("name", "unknown"))),
            "status": "args",
            "args": _preview(_decode_tool_args(args)),
        }
        emitted_args.add(key)
