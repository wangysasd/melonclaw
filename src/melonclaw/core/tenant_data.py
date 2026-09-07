"""演示环境的租户、用户和用户归属种子数据。"""

from __future__ import annotations

TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"tenant_id": "research", "tenant_name_zh": "研究"},
    {"tenant_id": "investment", "tenant_name_zh": "投资"},
    {"tenant_id": "trading", "tenant_name_zh": "交易"},
)

USER_SEEDS: tuple[dict[str, str], ...] = (
    {"user_id": "zhangsan", "user_name_zh": "张三"},
    {"user_id": "lisi", "user_name_zh": "李四"},
    {"user_id": "wangwu", "user_name_zh": "王五"},
    {"user_id": "zhaoliu", "user_name_zh": "赵六"},
    {"user_id": "sunqi", "user_name_zh": "孙琪"},
    {"user_id": "qianning", "user_name_zh": "钱宁"},
    {"user_id": "wujing", "user_name_zh": "吴静"},
    {"user_id": "zhoumei", "user_name_zh": "周梅"},
    {"user_id": "yangfan", "user_name_zh": "杨帆"},
    {"user_id": "heyu", "user_name_zh": "何宇"},
    {"user_id": "linan", "user_name_zh": "林安"},
    {"user_id": "chenxi", "user_name_zh": "陈希"},
)

USER_TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"user_id": "zhangsan", "tenant_id": "research"},
    {"user_id": "zhangsan", "tenant_id": "investment"},
    {"user_id": "wangwu", "tenant_id": "research"},
    {"user_id": "wujing", "tenant_id": "research"},
    {"user_id": "yangfan", "tenant_id": "research"},
    {"user_id": "lisi", "tenant_id": "investment"},
    {"user_id": "zhaoliu", "tenant_id": "investment"},
    {"user_id": "zhoumei", "tenant_id": "investment"},
    {"user_id": "heyu", "tenant_id": "investment"},
    {"user_id": "sunqi", "tenant_id": "trading"},
    {"user_id": "qianning", "tenant_id": "trading"},
    {"user_id": "linan", "tenant_id": "trading"},
    {"user_id": "chenxi", "tenant_id": "trading"},
)
