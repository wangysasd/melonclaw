"""明确声明的推理协议转换；展示块与供应商原始回传字段分别保存。"""

from typing import Any

REASONING_FORMATS = frozenset({"openai", "reasoning_content", "reasoning_details", "think_tags"})


class ThinkTagParser:
    """仅用于显式声明的 think_tags 协议；跨片标签最多暂存八个字符。"""

    def __init__(self) -> None:
        self.pending = ""
        self.thinking = False

    def feed(self, text: str, *, final: bool = False) -> list[dict[str, Any]]:
        self.pending += text
        blocks = []
        while self.pending:
            tag = "</think>" if self.thinking else "<think>"
            position = self.pending.find(tag)
            if position >= 0:
                value, self.pending = self.pending[:position], self.pending[position + len(tag):]
                if value:
                    blocks.append(self.block(value))
                self.thinking = not self.thinking
                continue
            keep = 0
            if not final:
                for size in range(1, min(len(tag), len(self.pending) + 1)):
                    if self.pending.endswith(tag[:size]):
                        keep = size
            end = len(self.pending) - keep
            if end:
                blocks.append(self.block(self.pending[:end]))
            self.pending = self.pending[end:]
            break
        return blocks

    def block(self, value: str) -> dict[str, Any]:
        return {"type": "reasoning", "reasoning": value} if self.thinking else {"type": "text", "text": value}


def reasoning_blocks(raw: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    blocks = []
    if mode == "reasoning_content" and isinstance(raw.get("reasoning_content"), str):
        if raw["reasoning_content"]:
            blocks.append({"type": "reasoning", "reasoning": raw["reasoning_content"]})
    elif mode == "reasoning_details":
        for detail in raw.get("reasoning_details", []):
            if isinstance(detail, dict):
                value = detail.get("text") or detail.get("summary")
                if isinstance(value, str) and value:
                    blocks.append({"type": "reasoning", "reasoning": value})
    content = raw.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    return blocks


def protocol_options(extra_config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    extra_body = dict(extra_config)
    options = extra_body.pop("_melonclaw", {})
    if not isinstance(options, dict) or set(options) - {"reasoning_format"}:
        raise ValueError("MelonClaw 推理协议配置不合法。")
    mode = options.get("reasoning_format", "openai")
    if not isinstance(mode, str) or mode not in REASONING_FORMATS:
        raise ValueError("不支持的推理协议。")
    return mode, extra_body
