"""数据库表名、默认值和 Schema 版本常量。"""

from __future__ import annotations

DEFAULT_PROJECT_NAME = "临时会话"
DEFAULT_PROJECT_SCHEMA_VERSION = "2026-09-07-default-project"

BUSINESS_TABLES = (
    "schema_migrations",
    "tenants",
    "users",
    "user_tenants",
    "projects",
    "chat_conversations",
    "chat_messages",
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
MULTITENANT_SCHEMA_VERSION = "2026-09-07-tenant-multitenant-v2"
PROJECT_SCHEMA_VERSION = "2026-09-07-project-workspaces"
MEMORY_SCHEMA_VERSION = "2026-09-07-memory-scopes-v2-operation-id"
CONVERSATION_SCHEMA_VERSION = "2026-09-07-conversation-user-owned-v1"
MODEL_SELECTION_SCHEMA_VERSION = "2026-09-10-system-model-selection-v1"
