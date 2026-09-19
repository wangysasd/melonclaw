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
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    ),
    Column(
        "tenant_id",
        String(64),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        primary_key=True,
    ),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("role", String(32), nullable=False, server_default="member"),
    Column("created_at", DateTime(timezone=True), nullable=False),
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
    # 模型绑定属于本次 assistant run，而不是 Conversation 全局配置。
    # 这些字段只保存非敏感快照，绝不保存 API Key。
    Column("model_id", String(160), nullable=True),
    Column("model_provider", String(80), nullable=True),
    Column("model_name", String(160), nullable=True),
    Column("model_display_name", String(120), nullable=True),
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

user_interactions = Table(
    "user_interactions",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "conversation_id",
        PGUUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "assistant_message_id",
        PGUUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("user_id", String(64), nullable=False),
    Column("interrupt_id", String(160), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String(24), nullable=False, server_default="waiting"),
    Column("decision_request_id", String(36), nullable=True),
    Column("answer", JSONB, nullable=True),
    Column("answer_digest", String(64), nullable=True),
    Column("reason_code", String(80), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "conversation_id",
        "interrupt_id",
        name="uq_user_interactions_conversation_interrupt",
    ),
    CheckConstraint(
        "status IN ('waiting', 'accepted', 'resolved', 'expired', 'discarded', 'recovery_required')",
        name="ck_user_interactions_status",
    ),
    Index(
        "ix_user_interactions_conversation_status",
        "conversation_id",
        "status",
    ),
    # 同一会话最多一张仍在等待回答的卡片。服务层已经强制一次只有一道题，
    # 这里补数据库兜底：将来放开多题或并发恢复时，重复卡片不会因为插入成功
    # 而静默出现两张。
    Index(
        "uq_user_interactions_active",
        "conversation_id",
        unique=True,
        postgresql_where=text("status = 'waiting'"),
    ),
)

chat_attachments = Table(
    "chat_attachments",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("project_id", PGUUID(as_uuid=True), nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("media_type", String(120), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("derived_size_bytes", Integer, nullable=False, server_default="0"),
    Column("sha256", String(64), nullable=False),
    Column("status", String(16), nullable=False, server_default="staged"),
    Column("parse_status", String(16), nullable=False),
    Column("parse_error_code", String(80), nullable=True),
    Column("parse_worker_id", String(120), nullable=True),
    Column("parse_lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("parse_attempts", Integer, nullable=False, server_default="0"),
    Column("client_request_id", String(36), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("storage_purged_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["project_id", "user_id"],
        ["projects.id", "projects.user_id"],
        name="fk_chat_attachments_project_user",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "kind IN ('image', 'pdf', 'text', 'document')",
        name="ck_chat_attachments_kind",
    ),
    CheckConstraint(
        "status IN ('staged', 'attached', 'expired', 'deleted')",
        name="ck_chat_attachments_status",
    ),
    CheckConstraint(
        "parse_status IN ('not_required', 'pending', 'processing', 'processed', 'failed')",
        name="ck_chat_attachments_parse_status",
    ),
    Index("ix_chat_attachments_project", "project_id"),
    Index("ix_chat_attachments_user_project", "user_id", "project_id"),
    Index("ix_chat_attachments_status_expires", "status", "expires_at"),
    Index(
        "ix_chat_attachments_parse_claim",
        "parse_status",
        "parse_lease_expires_at",
    ),
    Index(
        "uq_chat_attachments_upload_request",
        "user_id",
        "project_id",
        "client_request_id",
        unique=True,
        postgresql_where=text("client_request_id IS NOT NULL"),
    ),
)

chat_message_attachments = Table(
    "chat_message_attachments",
    metadata,
    Column(
        "message_id",
        PGUUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "attachment_id",
        PGUUID(as_uuid=True),
        ForeignKey("chat_attachments.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "message_id",
        "ordinal",
        name="uq_chat_message_attachments_message_ordinal",
    ),
    Index("ix_chat_message_attachments_attachment", "attachment_id"),
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
