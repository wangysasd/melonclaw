"""演示环境的租户、用户和用户归属种子数据。"""

from __future__ import annotations

TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"tenant_id": "wei", "tenant_name_zh": "魏"},
    {"tenant_id": "shu", "tenant_name_zh": "蜀"},
    {"tenant_id": "wu", "tenant_name_zh": "吴"},
)

USER_SEEDS: tuple[dict[str, str], ...] = (
    {"user_id": "caocao", "user_name_zh": "曹操"},
    {"user_id": "liubei", "user_name_zh": "刘备"},
    {"user_id": "sunquan", "user_name_zh": "孙权"},
)

USER_TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"user_id": "caocao", "tenant_id": "wei"},
    {"user_id": "liubei", "tenant_id": "shu"},
    {"user_id": "sunquan", "tenant_id": "wu"},
)

