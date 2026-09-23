"""数据库表名和默认值常量。"""

from __future__ import annotations

BUSINESS_TABLES = (
    "tenants",
    "users",
    "user_tenants",
    "projects",
    "chat_conversations",
    "chat_messages",
    "user_interactions",
    "chat_attachments",
    "chat_message_attachments",
    "memory_events",
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
