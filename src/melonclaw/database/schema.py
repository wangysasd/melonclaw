"""业务表定义。"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
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
    Column(
        "tenant_id",
        String(64),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("user_name_zh", String(3), nullable=False),
    Column("tenant_role", String(32), nullable=False, server_default="member"),
    Column("tenant_status", String(16), nullable=False, server_default="active"),
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
    Column("is_pinned", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("id", "user_id", name="uq_projects_id_user"),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.user_id"],
        name="fk_projects_user",
        ondelete="RESTRICT",
    ),
)

chat_conversations = Table(
    "chat_conversations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("project_id", PGUUID(as_uuid=True), nullable=True),
    UniqueConstraint("id", "user_id", name="uq_conversations_id_user"),
    ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
    Column("title", String(200), nullable=False, server_default="新会话"),
    Column("is_pinned", Boolean, nullable=False, server_default="false"),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("agent_id", String(120), nullable=False, server_default="quickstart-research-agent"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_conversations_project_updated", "user_id", "project_id", desc("updated_at"), desc("id")
    ),
    Index(
        "ix_conversations_unassigned_updated",
        "user_id",
        desc("updated_at"),
        desc("id"),
        postgresql_where=text("project_id IS NULL"),
    ),
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
    Column("assistant_steps", JSONB, nullable=False, server_default="[]"),
    Column("execution_duration_ms", BigInteger, nullable=True),
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
    Column("project_id", PGUUID(as_uuid=True), nullable=True),
    Column("owner_conversation_id", PGUUID(as_uuid=True), nullable=True),
    ForeignKeyConstraint(
        ["owner_conversation_id", "user_id"],
        ["chat_conversations.id", "chat_conversations.user_id"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "(project_id IS NULL) <> (owner_conversation_id IS NULL)", name="ck_attachment_owner"
    ),
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
    Index("ix_attachments_conversation", "user_id", "owner_conversation_id"),
    Index(
        "uq_attachments_conversation_upload",
        "user_id",
        "owner_conversation_id",
        "client_request_id",
        unique=True,
        postgresql_where=text(
            "owner_conversation_id IS NOT NULL AND client_request_id IS NOT NULL"
        ),
    ),
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
        postgresql_where=text("project_id IS NOT NULL AND client_request_id IS NOT NULL"),
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

skills = Table(
    "skills",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("name", String(64), nullable=False, unique=True),
    Column("scope", String(16), nullable=False),
    Column("source_type", String(16), nullable=False),
    Column(
        "created_by",
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    # 相对 data_root/skills 的路径，绝不存宿主机绝对路径。
    Column("storage_path", String(240), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("scope IN ('global', 'user', 'tenant')", name="ck_skills_scope"),
    CheckConstraint(
        "source_type IN ('builtin', 'upload', 'remote')",
        name="ck_skills_source_type",
    ),
    Index("ix_skills_scope_enabled", "scope", "enabled"),
    Index("ix_skills_created_by", "created_by"),
)

# 用户对共享（global）Skill 的个人启停偏好：默认启用（无行即启用），
# 用户写下 enabled=false 表示“我自己不用”，不影响其他用户。
# 私有 Skill 的启停仍由 skills.enabled 表达，不进这张表。
skill_user_states = Table(
    "skill_user_states",
    metadata,
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "skill_name",
        String(64),
        ForeignKey("skills.name", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

mcp_servers = Table(
    "mcp_servers",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("slug", String(64), nullable=False, unique=True),
    Column("scope", String(16), nullable=False),
    Column("source_type", String(16), nullable=False),
    Column("transport", String(16), nullable=False),
    Column("url", Text, nullable=True),
    Column("command", String(240), nullable=True),
    Column("args", JSONB, nullable=True),
    Column("env", JSONB, nullable=False, server_default="{}"),
    Column("headers", JSONB, nullable=False, server_default="{}"),
    Column("tool_allowlist", JSONB, nullable=True),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column(
        "created_by",
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope IN ('global', 'user', 'tenant')",
        name="ck_mcp_servers_scope",
    ),
    CheckConstraint(
        "source_type IN ('builtin', 'manual')",
        name="ck_mcp_servers_source_type",
    ),
    CheckConstraint(
        "transport IN ('http', 'sse', 'stdio')",
        name="ck_mcp_servers_transport",
    ),
    # 安全约束：stdio 只允许管理员发布的 global 配置，且必须有 command。
    CheckConstraint(
        "transport <> 'stdio' OR (scope = 'global' AND command IS NOT NULL)",
        name="ck_mcp_servers_stdio_scope",
    ),
    Index("ix_mcp_servers_scope_enabled", "scope", "enabled"),
    Index("ix_mcp_servers_created_by", "created_by"),
)

model_providers = Table(
    "model_providers",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    # 用户可见的稳定标识；模型配置通过 provider_key 引用供应商。
    Column("provider_key", String(64), nullable=False, unique=True),
    Column("scope", String(16), nullable=False),
    Column("source_type", String(16), nullable=False),
    Column("display_name", String(120), nullable=False),
    # 适配器分派键（如 openai_compatible），模型实例工厂按它选择实现。
    Column("provider_type", String(32), nullable=False, server_default="openai_compatible"),
    Column("base_url", String(400), nullable=False),
    # 凭据只写不回读：任何 API 都不返回该列。
    Column("api_key", String(240), nullable=True),
    Column("api_key_env", String(120), nullable=False, server_default=""),
    Column("request_headers", JSONB, nullable=False, server_default="{}"),
    Column("extra_config", JSONB, nullable=False, server_default="{}"),
    # 远端模型列表端点（如 https://api.deepseek.com/models），留空则不支持拉取。
    Column("models_endpoint", String(300), nullable=True),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column(
        "created_by",
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    # 配置版本：每次更新自增，进 Agent 缓存键使密钥轮换等变更即时生效。
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope IN ('global', 'user', 'tenant')",
        name="ck_model_providers_scope",
    ),
    CheckConstraint(
        "source_type IN ('system', 'manual')",
        name="ck_model_providers_source_type",
    ),
    Index("ix_model_providers_scope_enabled", "scope", "enabled"),
    Index("ix_model_providers_created_by", "created_by"),
)

model_configs = Table(
    "model_configs",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    # 用户可见的稳定标识；对外模型 ID 为 f"custom:{model_key}"，
    # 与内置 "system:" 前缀天然区分。
    Column("model_key", String(64), nullable=False, unique=True),
    # 归属的模型供应商；供应商下还有模型时删除被 RESTRICT 拒绝。
    Column(
        "provider_key",
        String(64),
        ForeignKey("model_providers.provider_key", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("scope", String(16), nullable=False),
    Column("source_type", String(16), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("model_name", String(160), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    # 默认模型：全局至多一个，每个用户的个人模型至多一个。
    Column("is_default", Boolean, nullable=False, server_default="false"),
    # 模型接受的输入形态（["text"] / ["text", "image"]），目录与运行时共用。
    Column("input_modalities", JSONB, nullable=False, server_default='["text"]'),
    Column(
        "created_by",
        String(64),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    # 配置版本：每次更新自增，进 Agent 缓存键使密钥轮换等变更即时生效。
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope IN ('global', 'user', 'tenant')",
        name="ck_model_configs_scope",
    ),
    CheckConstraint(
        "source_type IN ('system', 'manual')",
        name="ck_model_configs_source_type",
    ),
    Index("ix_model_configs_scope_enabled", "scope", "enabled"),
    Index("ix_model_configs_created_by", "created_by"),
    # 全局默认唯一，个人默认按创建者唯一。
    Index(
        "ux_model_configs_is_default",
        "is_default",
        unique=True,
        postgresql_where=text("is_default IS TRUE AND scope = 'global'"),
    ),
    Index(
        "ux_model_configs_user_default",
        "created_by",
        unique=True,
        postgresql_where=text("is_default IS TRUE AND scope = 'user'"),
    ),
)

provider_user_keys = Table(
    "provider_user_keys",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    # 归属的共享供应商；供应商删除时级联清掉各用户的 Key 覆盖。
    Column(
        "provider_key",
        String(64),
        ForeignKey("model_providers.provider_key", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "user_id",
        String(64),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    # 用户自己的 Key：只写不回读，API 只返回有无。
    Column("api_key", String(240), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("provider_key", "user_id", name="uq_provider_user_keys"),
    Index("ix_provider_user_keys_user", "user_id"),
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
