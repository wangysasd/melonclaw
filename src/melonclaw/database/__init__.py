"""数据库基础设施的统一入口。"""

from melonclaw.database.connection import (
    close_memory_store,
    derive_psycopg_database_url,
    normalize_async_database_url,
    open_checkpoint_pool,
    open_memory_store,
)
from melonclaw.database.constants import (
    BUSINESS_TABLES,
    CHECKPOINT_TABLES,
    CONVERSATION_SCHEMA_VERSION,
    DEFAULT_PROJECT_NAME,
    DEFAULT_PROJECT_SCHEMA_VERSION,
    MEMORY_SCHEMA_VERSION,
    MULTITENANT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
    STORE_TABLES,
)
from melonclaw.database.database import Database
from melonclaw.database.errors import (
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
)
from melonclaw.database.schema import (
    chat_conversations,
    chat_messages,
    memory_events,
    metadata,
    projects,
    schema_migrations,
    tenants,
    user_tenants,
    users,
)

__all__ = [
    "BUSINESS_TABLES",
    "CHECKPOINT_TABLES",
    "CONVERSATION_SCHEMA_VERSION",
    "DEFAULT_PROJECT_NAME",
    "DEFAULT_PROJECT_SCHEMA_VERSION",
    "MEMORY_SCHEMA_VERSION",
    "MULTITENANT_SCHEMA_VERSION",
    "PROJECT_SCHEMA_VERSION",
    "STORE_TABLES",
    "Database",
    "DatabaseConfigurationError",
    "DatabaseSchemaError",
    "DatabaseUnavailableError",
    "chat_conversations",
    "chat_messages",
    "close_memory_store",
    "derive_psycopg_database_url",
    "memory_events",
    "metadata",
    "normalize_async_database_url",
    "open_checkpoint_pool",
    "open_memory_store",
    "projects",
    "schema_migrations",
    "tenants",
    "user_tenants",
    "users",
]
