"""跨增量识别凭据，仅暂存可能构成凭据的后缀，不缓冲普通正文。"""

import re

from melonclaw.output.formatting import sanitize_text

_KEYS = (
    "deepseek_api_key", "tushare_mcp_token", "tavily_api_key", "openai_api_key",
    "anthropic_api_key", "langsmith_api_key", "api_key", "api-key", "access_token",
    "access-token", "apikey", "accesstoken", "secret", "password", "authorization",
)
_PREFIXES = {key[:size] for key in (*_KEYS, "sk-", "tvly-") for size in range(1, len(key) + 1)}
_START = re.compile(r"(?i)\b(?P<key>" + "|".join(_KEYS) + r")\b[\"']?\s*[:=]\s*")
_KEY_TAIL = re.compile(r"(?i)\b(?:" + "|".join(_KEYS) + r")\b[\"']?\s*$")
_TOKEN_TAIL = re.compile(r"\b(?:sk|tvly)-[A-Za-z0-9_-]*$")
_END = re.compile(r"[\s,;}\]]")
_AUTH_END = re.compile(r"[\r\n,;}\]]")


class StreamingRedactor:
    def __init__(self) -> None:
        self.pending = ""
        self.discard: str | None = None

    def feed(self, value: str, *, final: bool = False) -> str:
        if self.discard is not None:
            if self.discard in {"", "\n"}:
                end = (_AUTH_END if self.discard == "\n" else _END).search(value)
                closing = end.start() if end else -1
            else:
                closing = value.find(self.discard)
            if closing < 0:
                return ""
            value = value[closing:]
            self.discard = None
        self.pending += value
        cut = len(self.pending)
        incomplete = None
        for match in _START.finditer(self.pending):
            rest = self.pending[match.end():]
            quote = rest[:1] if rest[:1] in {"'", '"'} else ""
            terminator = _AUTH_END if match["key"].lower() == "authorization" else _END
            closed = rest.find(quote, 1) >= 0 if quote else terminator.search(rest) is not None
            if not rest or not closed:
                cut = min(cut, match.start())
                incomplete = match, quote
                break
        token = _TOKEN_TAIL.search(self.pending)
        if token and not final:
            cut = min(cut, token.start())
        if not final:
            key_tail = _KEY_TAIL.search(self.pending)
            if key_tail:
                cut = min(cut, key_tail.start())
            for size in range(1, min(64, len(self.pending)) + 1):
                suffix = self.pending[-size:].lower()
                if suffix in _PREFIXES and (size == len(self.pending) or not self.pending[-size-1].isalnum()):
                    cut = min(cut, len(self.pending) - size)
        if incomplete and (final or len(self.pending) - incomplete[0].end() > 4096):
            match, quote = incomplete
            result = sanitize_text(self.pending[:match.end()]) + quote + "<redacted>"
            self.pending = ""
            if not final:
                self.discard = quote or ("\n" if match["key"].lower() == "authorization" else "")
            return result
        if token and len(self.pending) - token.start() > 4096:
            result = sanitize_text(self.pending[:token.start()]) + "<redacted-token>"
            self.pending = ""
            if not final:
                self.discard = ""
            return result
        if final:
            cut = len(self.pending)
        result, self.pending = self.pending[:cut], self.pending[cut:]
        return sanitize_text(result)
