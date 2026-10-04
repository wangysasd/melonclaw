"""普通模型 content 原样进入展示，内部选择器在调用源头隔离。"""

import asyncio
from types import SimpleNamespace

from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.output.content import content_to_text
from melonclaw.output.events import _consume_messages


async def items(values):
    for value in values:
        yield value


class MessageStream:
    def __init__(self, values):
        self.values = values
        self.text = items(values)
        self.output = {"content": "".join(values), "tool_calls": []}

    def __aiter__(self):
        return items([
            {"event": "content-block-delta", "delta": {"type": "text-delta", "text": text}}
            for text in self.values
        ])


def test_incomplete_think_tags_are_forwarded_immediately_and_preserved_at_completion():
    raw = '<think>模型分析</think>{"tools": []}'
    for split in range(1, len(raw)):
        projector = AssistantStepAccumulator(message_id="a", run_id="r")
        step_id = projector.start_step()["step"]["id"]
        first = projector.project_text_delta(step_id, raw[:split])
        assert first["delta"] == raw[:split]
        projector.project_text_delta(step_id, raw[split:])
        events = projector.complete_step(step_id, content=raw)
        assert events[-1]["content"] == raw
        assert projector.terminal_snapshot(status="completed", final_content=raw)[0]["content"] == raw


def test_normal_json_content_is_preserved_without_guessing_its_source():
    async def run():
        queue = asyncio.Queue()
        answer = MessageStream(['<think>分析未结束', '{"tools": []}'])
        await _consume_messages(SimpleNamespace(messages=items([answer])), None, queue)
        return list(queue._queue)

    events = asyncio.run(run())
    assert "".join(e["text"] for e in events if e["type"] == "text") == '<think>分析未结束{"tools": []}'


def test_subagent_content_is_preserved():
    async def run():
        queue = asyncio.Queue()
        answer = MessageStream(['<think>分析</think>正文'])
        await _consume_messages(
            SimpleNamespace(messages=items([answer])),
            {"subagent_id": "child", "namespace": ("child",)}, queue,
        )
        return await queue.get()

    event = asyncio.run(run())
    assert event["type"] == "subagent_text"
    assert event["text"] == '<think>分析</think>正文'


def test_structured_text_content_includes_reasoning():
    assert content_to_text([
        {"type": "reasoning", "reasoning": "分析"},
        {"type": "text", "text": "正文"},
        {"type": "tool_call", "name": "write_file", "args": {"content": "工具参数"}},
    ]) == "分析正文"


def test_native_reasoning_deltas_survive_the_completed_snapshot():
    class NativeMessage:
        output = {"content": [
            {"type": "reasoning", "reasoning": "模型分析"},
            {"type": "text", "text": "正文"},
        ], "tool_calls": []}

        def __aiter__(self):
            return items([
                {"event": "content-block-delta", "delta": {"type": "reasoning-delta", "reasoning": "模型分析"}},
                {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "正文"}},
            ])

    async def run():
        queue = asyncio.Queue()
        projector = AssistantStepAccumulator(message_id="a", run_id="r")
        await _consume_messages(SimpleNamespace(messages=items([NativeMessage()])), None, queue, projector)
        return list(queue._queue)

    events = asyncio.run(run())
    assert "".join(e["delta"] for e in events if e["type"] == "assistant_text_delta") == "模型分析正文"
    assert next(e["content"] for e in events if e["type"] == "assistant_step_completed") == "模型分析正文"
