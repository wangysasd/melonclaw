"""展示层过滤器；不修改供模型继续推理的原始消息。"""

from __future__ import annotations

import json
import re


class VisibleTextFilter:
    """逐片过滤 think 块和独立的工具选择 JSON，支持标签任意分片。"""

    def __init__(self) -> None:
        self.pending = ""
        self.thinking = False
        self.prefix = ""
        self.body_started = False

    def feed(self, text: str) -> str:
        self.pending += text
        visible: list[str] = []
        while self.pending:
            tag = "</think>" if self.thinking else "<think>"
            index = self.pending.lower().find(tag)
            if index >= 0:
                if not self.thinking:
                    visible.append(self.pending[:index])
                self.pending = self.pending[index + len(tag):]
                self.thinking = not self.thinking
                continue
            # 保留可能横跨下一片的标签前缀，防止先把 '<thi' 发出去。
            keep = 0
            for size in range(1, min(len(tag), len(self.pending) + 1)):
                if self.pending.lower().endswith(tag[:size]):
                    keep = size
            end = len(self.pending) - keep
            if not self.thinking:
                visible.append(self.pending[:end])
            self.pending = self.pending[end:]
            break
        return self._body("".join(visible))

    def _body(self, text: str, *, final: bool = False) -> str:
        if self.body_started:
            return text
        self.prefix += text
        candidate = self.prefix.lstrip()
        compact = re.sub(r"\s+", "", candidate)
        if not final and ('{"tools"'.startswith(compact) or compact.startswith('{"tools"')):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                return ""
            if isinstance(parsed, dict) and set(parsed) == {"tools"} and isinstance(parsed["tools"], list):
                self.prefix = ""
                return ""
        result = self.prefix
        self.prefix = ""
        self.body_started = True
        return result

    def finish(self) -> str:
        # 未闭合的推理和不完整标签不作为正文补发。
        self.pending = ""
        return self._body("", final=True)


def visible_text(text: str) -> str:
    """用于最终展示正文；原始 checkpoint 消息保持不变。"""
    parser = VisibleTextFilter()
    return parser.feed(text) + parser.finish()
