"""MCP 凭据编码；数据库不保存 Headers/Env 明文，API 永不返回值。"""

from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken

from melonclaw.core.config import mcp_encryption_key

REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def _cipher() -> Fernet:
    try:
        return Fernet(mcp_encryption_key().encode())
    except (ValueError, TypeError):
        raise ValueError(
            "请配置有效的 MELONCLAW_MCP_ENCRYPTION_KEY 后再保存或使用 MCP 凭据。"
        ) from None


def encode_credentials(values: dict[str, str], scope: str) -> dict:
    return {
        key: (
            {"kind": "reference", "value": value}
            if scope == "global" and REFERENCE.fullmatch(value)
            else {"kind": "encrypted", "value": _cipher().encrypt(value.encode()).decode()}
        )
        for key, value in values.items()
    }


def decode_credentials(values: dict) -> dict[str, str]:
    try:
        if any(item["kind"] not in {"reference", "encrypted"} for item in values.values()):
            raise ValueError("MCP 凭据编码无效。")
        return {
            key: item["value"]
            if item["kind"] == "reference"
            else _cipher().decrypt(item["value"].encode()).decode()
            for key, item in values.items()
        }
    except (InvalidToken, KeyError, UnicodeError):
        raise ValueError("MCP 凭据无法解密，请检查部署密钥或重新配置凭据。") from None
