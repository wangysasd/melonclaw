"""HS256 JWT。载荷只有 sub 与 exp，密钥不进入令牌。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def issue_token(user_id: str, secret: str, ttl_hours: int, *, now: int | None = None) -> str:
    """签发只含用户和到期时间的令牌。"""

    issued = int(time.time()) if now is None else now
    header = _encode({"alg": "HS256", "typ": "JWT"})
    payload = _encode({"sub": user_id, "exp": issued + ttl_hours * 3600})
    signature = _encode_bytes(_digest(f"{header}.{payload}".encode(), secret))
    return f"{header}.{payload}.{signature}"


def token_subject(token: str, secret: str, *, now: float | None = None) -> str | None:
    """验签并读取未过期的 sub。失败、过期或载荷不合约定时返回 None，不查数据库。"""

    try:
        header_text, payload_text, signature_text = token.split(".")
    except ValueError:
        return None
    expected = _digest(f"{header_text}.{payload_text}".encode(), secret)
    actual = _decode(signature_text)
    if actual is None or len(actual) != len(expected) or not hmac.compare_digest(actual, expected):
        return None
    header = _json_object(_decode(header_text))
    payload = _json_object(_decode(payload_text))
    if header is None or payload is None or header.get("alg") != "HS256":
        return None
    if set(payload) != {"sub", "exp"}:
        return None
    subject, expires = payload["sub"], payload["exp"]
    if not isinstance(subject, str) or not subject or isinstance(expires, bool) or not isinstance(expires, int):
        return None
    current = time.time() if now is None else now
    if expires <= current:
        return None
    return subject


def _encode(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes | None:
    if not value:
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        return None


def _json_object(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _encode_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _digest(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
