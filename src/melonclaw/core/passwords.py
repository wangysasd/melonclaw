"""密码散列；明文仅在验证期间使用，不进入日志和持久化。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets


def hash_password(password: str) -> str:
    if not 5 <= len(password) <= 128:
        raise ValueError("密码需要 5～128 个字符。")
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${value}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or len(password) > 128:
        return False
    if not re.fullmatch(r"scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}", encoded):
        return False
    _, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)
