"""从模型协议事件报告生成阶段；完整转发模型文本，工具参数只报告生成阶段。"""

from collections.abc import AsyncIterator
from typing import Any

from melonclaw.output.stream_redaction import StreamingRedactor


async def model_text_with_activity(
    message_stream: Any, output: Any, *, report_activity: bool,
) -> AsyncIterator[tuple[str, str]]:
    """按原始事件顺序消费文本与工具参数，避免等待整条 AIMessage 才显示活动。"""

    phase = None
    file_call = False
    redactor = StreamingRedactor()
    content_kind = "text"
    async for event in message_stream:
        if event.get("event") != "content-block-delta":
            continue
        delta = event.get("delta", {})
        kind = delta.get("type")
        if kind == "text-delta":
            text = delta.get("text", "")
            if text:
                if content_kind != "text":
                    tail = redactor.feed("", final=True)
                    if tail:
                        yield content_kind, tail
                    redactor = StreamingRedactor()
                content_kind = "text"
                if report_activity and phase != "responding":
                    await output.put({"type": "run_phase", "phase": "responding"})
                phase = "responding"
                visible = redactor.feed(text)
                if visible:
                    yield "text", visible
            continue
        next_phase = None
        if kind == "reasoning-delta":
            next_phase = "thinking"
            reasoning = delta.get("reasoning", "")
            if reasoning:
                if content_kind != "reasoning":
                    tail = redactor.feed("", final=True)
                    if tail:
                        yield content_kind, tail
                    redactor = StreamingRedactor()
                content_kind = "reasoning"
                if report_activity and next_phase != phase:
                    await output.put({"type": "run_phase", "phase": next_phase})
                phase = next_phase
                visible = redactor.feed(reasoning)
                if visible:
                    yield "reasoning", visible
            continue
        if kind == "block-delta":
            fields = delta.get("fields", {})
            if fields.get("type") == "tool_call_chunk":
                # 名称通常只出现在首片；后续参数片沿用当前消息的文件生成阶段。
                file_call = file_call or fields.get("name") in {"write_file", "edit_file"}
                next_phase = "preparing_file" if file_call else "preparing_tools"
        if report_activity and next_phase and next_phase != phase:
            await output.put({"type": "run_phase", "phase": next_phase})
            phase = next_phase
    tail = redactor.feed("", final=True)
    if tail:
        yield content_kind, tail
