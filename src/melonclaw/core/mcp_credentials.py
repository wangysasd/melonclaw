"""MCP 凭据存储形状；数据库保存明文，管理 API 不回传值。"""

from __future__ import annotations

import re

REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def encode_credentials(values: dict[str, str], scope: str) -> dict:
    return {
        key: (
            {"kind": "reference", "value": value}
            if scope == "global" and REFERENCE.fullmatch(value)
            else {"kind": "plain", "value": value}
        )
        for key, value in values.items()
    }


def decode_credentials(values: dict) -> dict[str, str]:
    decoded = {}
    for key, item in values.items():
        if (
            not isinstance(item, dict)
            or item.get("kind") not in {"reference", "plain"}
            or not isinstance(item.get("value"), str)
        ):
            raise ValueError("MCP 凭据编码无效。")
        decoded[key] = item["value"]
    return decoded
