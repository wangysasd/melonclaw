import asyncio
import unittest
from types import SimpleNamespace

from melonclaw.output.events import _consume_messages
from melonclaw.output.visible_text import VisibleTextFilter, visible_text


async def items(values):
    for value in values:
        yield value


class VisibleTextTests(unittest.TestCase):
    def test_every_chunk_boundary(self):
        raw = '<think>internal planning</think>{"tools": []}'
        for split in range(len(raw) + 1):
            parser = VisibleTextFilter()
            self.assertEqual(parser.feed(raw[:split]) + parser.feed(raw[split:]) + parser.finish(), "")

    def test_character_chunks_and_multiple_blocks(self):
        parser = VisibleTextFilter()
        raw = '<think>private</think>你好<think>more private</think>世界'
        self.assertEqual("".join(parser.feed(c) for c in raw) + parser.finish(), "你好世界")

    def test_unclosed_reasoning_and_plain_content(self):
        self.assertEqual(visible_text("<think>unfinished"), "")
        self.assertEqual(visible_text("正文<think>unfinished"), "正文")
        self.assertEqual(visible_text('{"answer": "ok"}'), '{"answer": "ok"}')
        self.assertEqual(visible_text("1 < 2"), "1 < 2")

    def test_v3_selector_without_metadata_is_hidden(self):
        async def run():
            queue = asyncio.Queue()
            selector = SimpleNamespace(text=items(['<thi', 'nk>private</think>', '{"tools": []}']))
            answer = SimpleNamespace(text=items(['<think>private</think>', '正文']))
            await _consume_messages(SimpleNamespace(messages=items([selector, answer])), None, queue)
            return list(queue._queue)
        self.assertEqual(asyncio.run(run()), [
            {"type": "run_phase", "phase": "thinking"},
            {"type": "run_phase", "phase": "thinking"},
            {"type": "text", "text": "正文"},
        ])

    def test_selector_then_thinking_without_visible_text(self):
        async def run():
            queue = asyncio.Queue()
            selector = SimpleNamespace(metadata={"tool_selector": True}, text=items(['private']))
            answer = SimpleNamespace(text=items(['<think>still thinking']))
            await _consume_messages(SimpleNamespace(messages=items([selector, answer])), None, queue)
            return list(queue._queue)
        self.assertEqual(asyncio.run(run()), [
            {"type": "run_phase", "phase": "selecting_tools"},
            {"type": "run_phase", "phase": "thinking"},
            {"type": "run_phase", "phase": "thinking"},
        ])

    def test_subagent_is_filtered(self):
        async def run():
            queue = asyncio.Queue()
            answer = SimpleNamespace(text=items(['<think>private</think>正文']))
            await _consume_messages(
                SimpleNamespace(messages=items([answer])),
                {"subagent_id": "child", "namespace": ("child",)}, queue,
            )
            return await queue.get()
        event = asyncio.run(run())
        self.assertEqual(event["type"], "subagent_text")
        self.assertEqual(event["text"], "正文")
