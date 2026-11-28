"""中国大陆手机号规范化。完整号码只在当次请求中使用。"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[\s-]")
_NATIONAL = re.compile(r"^1[3-9]\d{9}$")


@dataclass(frozen=True)
class MainlandPhone:
    """同一次请求里使用的规范号码。不进入数据库或日志。"""

    e164: str
    national: str
    tail: str


def normalize_mainland_phone(value: str) -> MainlandPhone:
    """接受 11 位号码，以及 +86、86、0086 前缀。"""

    text = _SEPARATORS.sub("", value.strip())
    if text.startswith("+86"):
        text = text[3:]
    elif text.startswith("0086"):
        text = text[4:]
    elif text.startswith("86") and len(text) == 13:
        text = text[2:]
    if _NATIONAL.fullmatch(text) is None:
        raise ValueError("请输入中国大陆手机号。")
    return MainlandPhone(e164=f"+86{text}", national=text, tail=text[-4:])


def phone_digest(e164: str, key: str) -> str:
    """用部署保存的密钥计算检索摘要。轮换密钥需要单独迁移。"""

    return hmac.new(key.encode(), e164.encode(), hashlib.sha256).hexdigest()
