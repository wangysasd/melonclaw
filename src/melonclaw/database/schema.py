"""业务表定义。"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

metadata = MetaData()

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("version", String(120), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

tenants = Table(
    "tenants",
    metadata,
    Column("tenant_id", String(64), primary_key=True),
    Column("tenant_name_zh", String(5), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(64), primary_key=True),
    Column("user_name_zh", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

user_tenants = Table(
    "user_tenants",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "tenant_id",
        String(64),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("role", String(32), nullable=False, server_default="member"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "user_id",
        "tenant_id",
        name="uq_user_tenants_user_tenant",
    ),
)

projects = Table(
    "projects",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("name", String(120), nullable=False),
    Column("workdir_path", String(240), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("is_default", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("id", "user_id", name="uq_projects_id_user"),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.user_id"],
        name="fk_projects_user",
        ondelete="RESTRICT",
    ),
    Index(
        "uq_projects_user_default",
        "user_id",
        unique=True,
        postgresql_where=text("is_default"),
    ),
)

chat_conversations = Table(
    "chat_conversations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("project_id", PGUUID(as_uuid=True), nullable=False),
    Column("title", String(200), nullable=False, server_default="新会话"),
    Column("agent_id", String(120), nullable=False, server_default="quickstart-research-agent"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_chat_conversations_user_updated_id",
        "user_id",
        desc("updated_at"),
        desc("id"),
    ),
    ForeignKeyConstraint(
        ["project_id", "user_id"],
        ["projects.id", "projects.user_id"],
        name="fk_chat_conversations_project_user",
        ondelete="RESTRICT",
    ),
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "conversation_id",
        PGUUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("seq", Integer, nullable=False),
    Column("request_id", String(36), nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False, server_default=""),
    Column("status", String(16), nullable=False),
    Column("display_metadata", JSONB, nullable=False, server_default="{}"),
    Column("error_code", String(80), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_conversation_seq"),
    UniqueConstraint(
        "conversation_id",
        "request_id",
        "role",
        name="uq_chat_messages_conversation_request_role",
    ),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
    CheckConstraint(
        "status IN ('pending', 'completed', 'failed', 'cancelled', 'interrupted')",
        name="ck_chat_messages_status",
    ),
    Index("ix_chat_messages_conversation_seq", "conversation_id", "seq"),
)

memory_events = Table(
    "memory_events",
    metadata,
    Column("event_id", PGUUID(as_uuid=True), primary_key=True),
    Column("scope_type", String(16), nullable=False),
    Column("scope_id", String(128), nullable=False),
    Column("agent_id", String(120), nullable=False),
    Column("key", String(120), nullable=False),
    Column("operation", String(32), nullable=False),
    Column("actor_user_id", String(64), nullable=True),
    Column("tenant_id", String(64), nullable=True),
    Column("request_id", String(80), nullable=True),
    Column("operation_id", String(160), nullable=True),
    Column("run_id", String(80), nullable=True),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("content_hash", String(64), nullable=True),
    Column("metadata", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope_type IN ('global', 'tenant', 'user')",
        name="ck_memory_events_scope_type",
    ),
    Index(
        "ix_memory_events_scope_key_created",
        "scope_type",
        "scope_id",
        "agent_id",
        "key",
        desc("created_at"),
    ),
)

