"""数据库表名和默认值常量。"""

from __future__ import annotations

BUSINESS_TABLES = (
    "tenants",
    "users",
    "projects",
    "chat_conversations",
    "chat_messages",
    "user_interactions",
    "chat_attachments",
    "chat_message_attachments",
    "memory_events",
    # 资源表同样由 schema.py 派生，必须一起纳入 verify_schema 的列校验：
    # create_all 不会修补已有表，漏检会让结构漂移一路带到种子 INSERT 才以
    # 裸数据库错误暴露（例如 model_configs 缺 is_default 列）。
    "skills",
    "skill_user_states",
    "mcp_servers",
    "mcp_user_preferences",
    "model_providers",
    "model_configs",
    "provider_user_keys",
)
CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
STORE_TABLES = (
    "store_migrations",
    "store",
)
