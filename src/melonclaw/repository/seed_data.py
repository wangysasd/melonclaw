"""演示环境的租户和唯一租户用户种子数据。"""

from __future__ import annotations

TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"tenant_id": "wei", "tenant_name_zh": "魏"},
    {"tenant_id": "shu", "tenant_name_zh": "蜀"},
    {"tenant_id": "wu", "tenant_name_zh": "吴"},
)

USER_SEEDS: tuple[dict[str, str], ...] = (
    {
        "user_id": "caocao",
        "user_name_zh": "曹操",
        "tenant_id": "wei",
        "tenant_role": "member",
        "tenant_status": "active",
    },
    {
        "user_id": "liubei",
        "user_name_zh": "刘备",
        "tenant_id": "shu",
        "tenant_role": "member",
        "tenant_status": "active",
    },
    {
        "user_id": "sunquan",
        "user_name_zh": "孙权",
        "tenant_id": "wu",
        "tenant_role": "member",
        "tenant_status": "active",
    },
)
