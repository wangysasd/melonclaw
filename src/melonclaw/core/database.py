"""MelonClaw 的 PostgreSQL 连接、业务表和会话级并发控制。"""

from __future__ import annotations

import base64
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
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
    and_,
    case,
    desc,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from melonclaw.core.tenant_data import (
    TENANT_SEEDS,
    USER_SEEDS,
    USER_TENANT_SEEDS,
)


class DatabaseConfigurationError(RuntimeError):
    """DATABASE_URL 缺失或驱动配置不符合当前应用要求。"""


class DatabaseUnavailableError(RuntimeError):
    """PostgreSQL 当前不可连接或无法执行基础查询。"""


class DatabaseSchemaError(RuntimeError):
    """初始化命令尚未创建应用所需的表。"""


class ConversationNotFoundError(LookupError):
    """会话不存在或不属于请求中的 user_id。"""

    def __init__(self) -> None:
        super().__init__("会话不存在或不属于当前用户。")


class ProjectNotFoundError(LookupError):
    """Project 不存在或不属于请求中的用户。"""

    def __init__(self) -> None:
        super().__init__("Project 不存在或不属于当前用户。")


class ConversationBusyError(RuntimeError):
    """同一会话已有另一个 Agent 执行。"""


class AssistantStateConflictError(RuntimeError):
    """助手消息已经离开预期状态，拒绝旧执行覆盖新状态。"""


class RequestConflictError(RuntimeError):
    """相同 request_id 的正文与第一次请求不一致。"""


@dataclass(frozen=True)
class UserContext:
    """经过数据库校验的用户和租户上下文。"""

    user_id: str
    user_name_zh: str
    tenant_id: str
    tenant_name_zh: str
    tenant_role: str = "member"
    tenant_status: str = "active"


DEFAULT_PROJECT_NAME = "临时会话"
DEFAULT_SIMULATED_USER_ID = "zhangsan"
DEFAULT_PROJECT_SCHEMA_VERSION = "2026-09-07-default-project"


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


def normalize_async_database_url(raw_url: str) -> str:
    """校验业务连接使用 asyncpg，并保留 URL 中的其余配置。"""

    if not raw_url.strip():
        raise DatabaseConfigurationError("未配置 DATABASE_URL。")
    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise DatabaseConfigurationError("DATABASE_URL 格式无效。") from exc
    if url.drivername != "postgresql+asyncpg":
        raise DatabaseConfigurationError(
            "DATABASE_URL 必须使用 postgresql+asyncpg 驱动。"
        )
    return url.render_as_string(hide_password=False)


def derive_psycopg_database_url(raw_url: str) -> str:
    """从同一 DATABASE_URL 派生 psycopg URL，不重复维护数据库地址。"""

    async_url = normalize_async_database_url(raw_url)
    url = make_url(async_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def open_checkpoint_pool(raw_url: str) -> AsyncConnectionPool:
    """打开供 AsyncPostgresSaver 使用的 psycopg 异步连接池。"""

    connection_url = derive_psycopg_database_url(raw_url)
    pool = AsyncConnectionPool(
        connection_url,
        min_size=1,
        max_size=8,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        name="melonclaw-checkpointer",
    )
    try:
        await pool.open(wait=True)
    except Exception as exc:
        await pool.close()
        raise DatabaseUnavailableError(
            "PostgreSQL Checkpointer 连接失败，请检查 DATABASE_URL 和 PostgreSQL 认证配置。"
        ) from exc
    return pool


async def open_memory_store(
    raw_url: str,
) -> tuple[Any, AsyncPostgresStore]:
    """打开长期 Memory 使用的异步 PostgreSQL Store。"""

    connection_url = derive_psycopg_database_url(raw_url)
    context_manager = AsyncPostgresStore.from_conn_string(
        connection_url,
        pool_config={"min_size": 1, "max_size": 8},
    )
    try:
        store = await context_manager.__aenter__()
    except Exception as exc:
        raise DatabaseUnavailableError(
            "PostgreSQL Memory Store 连接失败，请检查 DATABASE_URL 和 PostgreSQL 配置。"
        ) from exc
    return context_manager, store


async def close_memory_store(context_manager: Any) -> None:
    """关闭由 ``open_memory_store`` 创建的异步 Store 和连接池。"""

    if context_manager is not None:
        await context_manager.__aexit__(None, None, None)


@dataclass(frozen=True)
class RequestRecord:
    """一次 request_id 对应的业务消息对。"""

    request_id: str
    content: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]


@dataclass(frozen=True)
class PreparedMessagePair:
    """短事务提交后的消息 ID，供流式执行和最终更新使用。"""

    request_id: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


def _as_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _conversation_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    project_name = row.get("project_name") if hasattr(row, "get") else None
    workdir_path = row.get("workdir_path") if hasattr(row, "get") else None
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "project_id": str(row["project_id"]),
        "project_name": str(project_name or DEFAULT_PROJECT_NAME),
        "workdir_path": str(workdir_path or ""),
        "title": str(row["title"]),
        "agent_id": str(row["agent_id"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _user_dict(
    row: Mapping[str, Any],
    *,
    tenant_ids: list[str] | None = None,
    tenant_names: list[str] | None = None,
    tenant_memberships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    user_name = str(row["user_name_zh"])
    normalized_tenant_ids = tenant_ids or [str(row["tenant_id"])]
    normalized_tenant_names = tenant_names or [str(row["tenant_name_zh"])]
    tenant_name = "、".join(normalized_tenant_names)
    default_tenant_id = normalized_tenant_ids[0]
    return {
        "user_id": str(row["user_id"]),
        # username 保留现有开发接口字段，值改为用户中文名。
        "username": user_name,
        "user_name_zh": user_name,
        # 保留 tenant_id 作为当前开发上下文的默认租户；完整关系见 tenant_ids。
        "tenant_id": default_tenant_id,
        "default_tenant_id": default_tenant_id,
        "tenant_ids": normalized_tenant_ids,
        "tenant_names": normalized_tenant_names,
        "tenant_name": tenant_name,
        "tenant_name_zh": tenant_name,
        "tenant_role": str(row.get("role") or "member"),
        "tenant_status": str(row.get("status") or "active"),
        "tenant_memberships": tenant_memberships or [
            {
                "tenant_id": normalized_tenant_ids[0],
                "tenant_name": normalized_tenant_names[0],
                "role": str(row.get("role") or "member"),
                "status": str(row.get("status") or "active"),
            }
        ],
        "is_default": str(row["user_id"]) == DEFAULT_SIMULATED_USER_ID,
        "display_name": f"{user_name}-{tenant_name}",
    }


def _project_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "name": str(row["name"]),
        "workdir_path": str(row["workdir_path"]),
        "status": str(row["status"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
        "is_default": bool(row["is_default"]),
    }


def _message_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "seq": int(row["seq"]),
        "request_id": str(row["request_id"]),
        "role": str(row["role"]),
        "content": str(row["content"] or ""),
        "status": str(row["status"]),
        "display_metadata": row["display_metadata"] or {},
        "error_code": row["error_code"],
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _conversation_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:30] or "新会话"


def _default_project_id(user_id: str) -> UUID:
    """为用户生成可重复计算的默认 Project ID。"""

    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:default-project:{user_id}",
    )


def _user_tenant_id(user_id: str, tenant_id: str) -> UUID:
    """为演示环境的用户租户关系生成稳定的代理主键。"""

    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:user-tenant:{user_id}:{tenant_id}",
    )


def encode_conversation_cursor(updated_at: str, conversation_id: str) -> str:
    payload = json.dumps(
        {"updated_at": updated_at, "id": conversation_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_conversation_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at = datetime.fromisoformat(value["updated_at"])
        conversation_id = UUID(value["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cursor 无效。") from exc
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at, conversation_id


class BusinessDatabase:
    """使用 SQLAlchemy AsyncEngine 访问聊天业务表。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine: AsyncEngine | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("业务数据库尚未打开。")
        return self._engine

    async def open(self) -> None:
        if self._engine is not None:
            return
        async_url = normalize_async_database_url(self.database_url)
        engine = create_async_engine(
            async_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            await engine.dispose()
            raise DatabaseUnavailableError(
                "业务数据库连接失败，请检查 DATABASE_URL 和 PostgreSQL 认证配置。"
            ) from exc
        self._engine = engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def create_schema(self) -> None:
        """由独立初始化命令调用；API lifespan 不会自动迁移。"""

        async with self.engine.begin() as connection:
            # 先创建租户标签基础表。旧版 chat_conversations 可能还没有
            # project_id，因此不能直接让 create_all 尝试创建新索引。
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    tables=[schema_migrations, tenants, users, user_tenants],
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE user_tenants "
                    "ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE user_tenants "
                    "ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'member'"
                )
            )
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    tables=[memory_events],
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE memory_events ADD COLUMN IF NOT EXISTS "
                    "operation_id VARCHAR(160)"
                )
            )
            projects_table_exists = await self._table_exists(connection, "projects")
            if not projects_table_exists:
                await connection.run_sync(
                    lambda sync_connection: metadata.create_all(
                        sync_connection,
                        tables=[projects],
                    )
                )
            await connection.execute(
                text(
                    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                    "is_default BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            conversation_table_exists = await self._table_exists(
                connection,
                "chat_conversations",
            )
            if conversation_table_exists:
                await connection.execute(
                    text(
                        "ALTER TABLE chat_conversations "
                        "ADD COLUMN IF NOT EXISTS project_id UUID"
                    )
                )

            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_projects_user_default "
                    "ON projects (user_id) WHERE is_default"
                )
            )
            if not await self._constraint_exists(connection, "uq_projects_id_user"):
                await connection.execute(
                    text(
                        "ALTER TABLE projects ADD CONSTRAINT uq_projects_id_user "
                        "UNIQUE (id, user_id)"
                    )
                )
            await self._seed_tenant_data(connection)
            await self._ensure_default_projects(connection)
            if not conversation_table_exists:
                # 直到 projects 的 (id, user_id) 唯一约束建立后，才能创建
                # 引用它的 Conversation 联合外键。
                await connection.run_sync(
                    lambda sync_connection: metadata.create_all(
                        sync_connection,
                        tables=[chat_conversations, chat_messages],
                    )
                )
            await self._complete_conversation_migration(
                connection,
                conversation_table_exists=conversation_table_exists,
            )
            # 现在旧表也已经补齐 project_id，可以安全创建新索引。
            await connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    tables=[projects, chat_conversations, chat_messages, memory_events],
                )
            )

    @staticmethod
    async def _table_exists(connection: AsyncConnection, table_name: str) -> bool:
        result = await connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :table_name"
                ")"
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar())

    @staticmethod
    async def _column_exists(
        connection: AsyncConnection,
        *,
        table_name: str,
        column_name: str,
    ) -> bool:
        result = await connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = :table_name "
                "AND column_name = :column_name"
                ")"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return bool(result.scalar())

    @staticmethod
    async def _constraint_exists(
        connection: AsyncConnection,
        constraint_name: str,
    ) -> bool:
        result = await connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = :name)"),
            {"name": constraint_name},
        )
        return bool(result.scalar())

    @staticmethod
    async def _seed_tenant_data(connection: AsyncConnection) -> None:
        timestamp = _now()
        await connection.execute(
            text(
                "INSERT INTO tenants "
                "(tenant_id, tenant_name_zh, created_at) "
                "VALUES (:tenant_id, :tenant_name_zh, :created_at) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "tenant_name_zh = EXCLUDED.tenant_name_zh"
            ),
            [{**item, "created_at": timestamp} for item in TENANT_SEEDS],
        )
        await connection.execute(
            text(
                "INSERT INTO users (user_id, user_name_zh, created_at) "
                "VALUES (:user_id, :user_name_zh, :created_at) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "user_name_zh = EXCLUDED.user_name_zh"
            ),
            [{**item, "created_at": timestamp} for item in USER_SEEDS],
        )
        await connection.execute(
            text(
                "INSERT INTO user_tenants (id, user_id, tenant_id, created_at) "
                "VALUES (:id, :user_id, :tenant_id, :created_at) "
                "ON CONFLICT (user_id, tenant_id) DO NOTHING"
            ),
            [
                {
                    **item,
                    "id": _user_tenant_id(item["user_id"], item["tenant_id"]),
                    "created_at": timestamp,
                }
                for item in USER_TENANT_SEEDS
            ],
        )

    @staticmethod
    async def _ensure_default_projects(connection: AsyncConnection) -> None:
        """为每个已有用户补齐唯一、可重复初始化的默认 Project。"""

        timestamp = _now()
        for user in USER_SEEDS:
            user_id = user["user_id"]
            existing = await connection.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE user_id = :user_id AND is_default "
                    "ORDER BY id LIMIT 1"
                ),
                {"user_id": user_id},
            )
            existing_id = existing.scalar()
            if existing_id is not None:
                await connection.execute(
                    text(
                        "UPDATE projects SET name = :name, status = 'active', "
                        "updated_at = :updated_at WHERE id = :id"
                    ),
                    {
                        "id": existing_id,
                        "name": DEFAULT_PROJECT_NAME,
                        "updated_at": timestamp,
                    },
                )
                continue
            project_id = _default_project_id(user_id)
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, name, workdir_path, created_at, updated_at, "
                    "status, is_default) "
                    "VALUES (:id, :user_id, :name, :workdir_path, :created_at, "
                    ":updated_at, 'active', TRUE) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": project_id,
                    "user_id": user_id,
                    "name": DEFAULT_PROJECT_NAME,
                    "workdir_path": f"projects/{project_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )

    async def ensure_default_project(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """幂等返回用户的“临时会话” Project。"""

        project_id = _default_project_id(user_id)
        timestamp = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, name, workdir_path, "
                    "created_at, updated_at, status, is_default) "
                    "VALUES (:id, :user_id, :name, :workdir_path, "
                    ":created_at, :updated_at, 'active', TRUE) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": project_id,
                    "user_id": user_id,
                    "name": DEFAULT_PROJECT_NAME,
                    "workdir_path": f"projects/{project_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            result = await connection.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE user_id = :user_id AND is_default "
                    "ORDER BY id LIMIT 1"
                ),
                {"user_id": user_id},
            )
            selected_id = result.scalar()
        project = (
            await self.get_project(UUID(str(selected_id)), user_id)
            if selected_id
            else None
        )
        if project is None:
            raise DatabaseSchemaError("无法创建或读取用户默认 Project。")
        return project

    async def _complete_conversation_migration(
        self,
        connection: AsyncConnection,
        *,
        conversation_table_exists: bool,
    ) -> None:
        if conversation_table_exists:
            await connection.execute(
                text(
                    "UPDATE chat_conversations AS conversations "
                    "SET project_id = defaults.id "
                    "FROM projects AS defaults "
                    "WHERE conversations.project_id IS NULL "
                    "AND defaults.user_id = conversations.user_id "
                    "AND defaults.is_default"
                )
            )
            # 上一版 Project 迁移曾为每个历史会话创建一个“历史会话”项目。
            # 这些记录属于 Project 功能上线前的会话，统一收敛到用户默认 Project。
            await connection.execute(
                text(
                    "UPDATE chat_conversations AS conversations "
                    "SET project_id = defaults.id "
                    "FROM projects AS legacy "
                    "JOIN projects AS defaults "
                    "ON defaults.user_id = legacy.user_id "
                    "AND defaults.is_default "
                    "WHERE conversations.project_id = legacy.id "
                    "AND legacy.name = '历史会话' "
                    "AND legacy.workdir_path = 'projects/' || legacy.id::text"
                )
            )
            await connection.execute(
                text(
                    "UPDATE projects AS legacy "
                    "SET status = 'deleted' "
                    "WHERE legacy.name = '历史会话' "
                    "AND legacy.workdir_path = 'projects/' || legacy.id::text "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM chat_conversations AS conversations "
                    "WHERE conversations.project_id = legacy.id"
                    ")"
                )
            )
            invalid_project_result = await connection.execute(
                text(
                    "SELECT conversations.id "
                    "FROM chat_conversations AS conversations "
                    "LEFT JOIN projects "
                    "ON projects.id = conversations.project_id "
                    "AND projects.user_id = conversations.user_id "
                    "WHERE projects.id IS NULL "
                    "LIMIT 1"
                )
            )
            invalid_project = invalid_project_result.scalar()
            if invalid_project is not None:
                raise DatabaseSchemaError(
                    "chat_conversations 中存在无法匹配 Project 的记录："
                    f"{invalid_project}。请先修复数据后再初始化。"
                )
            await connection.execute(
                text(
                    "ALTER TABLE chat_conversations "
                    "ALTER COLUMN project_id SET NOT NULL"
                )
            )

        # Conversation 的 Project ownership 只使用 (project_id, user_id)。
        for old_name in ("fk_chat_conversations_project",):
            if await self._constraint_exists(connection, old_name):
                await connection.execute(
                    text(
                        "ALTER TABLE chat_conversations DROP CONSTRAINT "
                        f"{old_name}"
                    )
                )

        # Conversation 只属于 User + Project。旧版本曾增加 tenant_id；保留旧列
        # 以避免历史数据库发生数据丢失，但移除其外键并停止在业务层读取/写入。
        if await self._constraint_exists(
            connection,
            "fk_chat_conversations_user_tenant",
        ):
            await connection.execute(
                text(
                    "ALTER TABLE chat_conversations DROP CONSTRAINT "
                    "fk_chat_conversations_user_tenant"
                )
            )
        if await self._column_exists(
            connection,
            table_name="chat_conversations",
            column_name="tenant_id",
        ):
            await connection.execute(
                text(
                    "COMMENT ON COLUMN chat_conversations.tenant_id IS "
                    "'兼容历史数据；当前业务不读取、不写入、不按此字段过滤。"
                    "Conversation 归属由 user_id + project_id 决定。'"
                )
            )

        constraints = {
            "fk_chat_conversations_project_user": (
                "ALTER TABLE chat_conversations ADD CONSTRAINT "
                "fk_chat_conversations_project_user FOREIGN KEY "
                "(project_id, user_id) REFERENCES projects "
                "(id, user_id) ON DELETE RESTRICT"
            ),
        }
        for name, ddl in constraints.items():
            if not await self._constraint_exists(connection, name):
                await connection.execute(text(ddl))

        migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": MULTITENANT_SCHEMA_VERSION},
        )
        if not migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=MULTITENANT_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

        project_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": PROJECT_SCHEMA_VERSION},
        )
        if not project_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=PROJECT_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

        default_project_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": DEFAULT_PROJECT_SCHEMA_VERSION},
        )
        if not default_project_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=DEFAULT_PROJECT_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

        memory_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": MEMORY_SCHEMA_VERSION},
        )
        if not memory_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=MEMORY_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

        conversation_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": CONVERSATION_SCHEMA_VERSION},
        )
        if not conversation_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=CONVERSATION_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

    async def verify_schema(
        self,
        *,
        require_checkpointer: bool = True,
        require_store: bool = False,
    ) -> None:
        expected = list(BUSINESS_TABLES)
        if require_checkpointer:
            expected.extend(CHECKPOINT_TABLES)
        if require_store:
            expected.extend(STORE_TABLES)
        placeholders = ", ".join(f":table_{index}" for index in range(len(expected)))
        query = text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN (" + placeholders + ")"
        )
        bind_values = {f"table_{index}": name for index, name in enumerate(expected)}
        async with self.engine.connect() as connection:
            result = await connection.execute(query, bind_values)
            found = {str(row[0]) for row in result.fetchall()}
        missing = [name for name in expected if name not in found]
        if missing:
            required = "、".join(missing)
            raise DatabaseSchemaError(
                f"数据库尚未初始化，缺少表：{required}。请先运行 uv run melonclaw-db-init。"
            )

    async def list_users(self) -> list[dict[str, Any]]:
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                tenants.c.tenant_id,
                tenants.c.tenant_name_zh,
                user_tenants.c.role,
                user_tenants.c.status,
            )
            .select_from(
                users.join(
                    user_tenants,
                    users.c.user_id == user_tenants.c.user_id,
                ).join(
                    tenants,
                    user_tenants.c.tenant_id == tenants.c.tenant_id,
                )
            )
            .where(user_tenants.c.status == "active")
            .order_by(
                case(
                    (users.c.user_id == DEFAULT_SIMULATED_USER_ID, 0),
                    else_=1,
                ),
                users.c.user_id.asc(),
                tenants.c.tenant_id.asc(),
            )
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_id = str(row["user_id"])
            item = grouped.setdefault(
                user_id,
                {
                    "row": row,
                    "tenant_ids": [],
                    "tenant_names": [],
                    "tenant_memberships": [],
                },
            )
            item["tenant_ids"].append(str(row["tenant_id"]))
            item["tenant_names"].append(str(row["tenant_name_zh"]))
            item["tenant_memberships"].append(
                {
                    "tenant_id": str(row["tenant_id"]),
                    "tenant_name": str(row["tenant_name_zh"]),
                    "role": str(row.get("role") or "member"),
                    "status": str(row.get("status") or "active"),
                }
            )
        return [
            _user_dict(
                item["row"],
                tenant_ids=item["tenant_ids"],
                tenant_names=item["tenant_names"],
                tenant_memberships=item["tenant_memberships"],
            )
            for item in grouped.values()
        ]

    async def get_user_context(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> UserContext | None:
        query = (
            select(
                users.c.user_id,
                users.c.user_name_zh,
                tenants.c.tenant_id,
                tenants.c.tenant_name_zh,
                user_tenants.c.role,
                user_tenants.c.status,
            )
            .select_from(
                users.join(
                    user_tenants,
                    users.c.user_id == user_tenants.c.user_id,
                ).join(
                    tenants,
                    user_tenants.c.tenant_id == tenants.c.tenant_id,
                )
            )
            .where(users.c.user_id == user_id)
            .where(user_tenants.c.status == "active")
            .order_by(user_tenants.c.tenant_id.asc())
        )
        if tenant_id is not None:
            query = query.where(user_tenants.c.tenant_id == tenant_id)
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        return UserContext(
            user_id=str(row["user_id"]),
            user_name_zh=str(row["user_name_zh"]),
            tenant_id=str(row["tenant_id"]),
            tenant_name_zh=str(row["tenant_name_zh"]),
            tenant_role=str(row.get("role") or "member"),
            tenant_status=str(row.get("status") or "active"),
        )

    async def create_project(
        self,
        user_id: str,
        name: str,
    ) -> dict[str, Any]:
        """创建当前用户的 Project；tenant 只作为用户标签，不参与归属。"""

        project_id = uuid4()
        timestamp = _now()
        values = {
            "id": project_id,
            "user_id": user_id,
            "name": name,
            "workdir_path": f"projects/{project_id}",
            "created_at": timestamp,
            "updated_at": timestamp,
            "status": "active",
            "is_default": False,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(projects).values(**values))
        return _project_dict(values)

    async def get_project(
        self,
        project_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        query = select(projects).where(
            and_(
                projects.c.id == project_id,
                projects.c.user_id == user_id,
                projects.c.status == "active",
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _project_dict(row) if row else None

    async def list_projects(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        query = (
            select(projects)
            .where(
                and_(
                    projects.c.user_id == user_id,
                    projects.c.status == "active",
                )
            )
            .order_by(projects.c.updated_at.desc(), projects.c.id.desc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_project_dict(row) for row in rows]

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID,
    ) -> dict[str, Any]:
        conversation_id = uuid4()
        timestamp = _now()
        values = {
            "id": conversation_id,
            "user_id": user_id,
            "project_id": project_id,
            "title": "新会话",
            "agent_id": "quickstart-research-agent",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(chat_conversations).values(**values))
        conversation = await self.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise DatabaseSchemaError("刚创建的会话无法读取，请检查 Project 归属。")
        return conversation

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        query = (
            select(
                chat_conversations,
                projects.c.name.label("project_name"),
                projects.c.workdir_path,
            )
            .select_from(
                chat_conversations.join(
                    projects,
                    and_(
                        chat_conversations.c.project_id == projects.c.id,
                        chat_conversations.c.user_id == projects.c.user_id,
                    ),
                )
            )
            .where(
                and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _conversation_dict(row) if row else None

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        conditions = [
            chat_conversations.c.user_id == user_id,
        ]
        if project_id is not None:
            conditions.append(chat_conversations.c.project_id == project_id)
        if cursor:
            cursor_updated_at, cursor_id = decode_conversation_cursor(cursor)
            conditions.append(
                or_(
                    chat_conversations.c.updated_at < cursor_updated_at,
                    and_(
                        chat_conversations.c.updated_at == cursor_updated_at,
                        chat_conversations.c.id < cursor_id,
                    ),
                )
            )
        query = (
            select(
                chat_conversations,
                projects.c.name.label("project_name"),
                projects.c.workdir_path,
            )
            .select_from(
                chat_conversations.join(
                    projects,
                    and_(
                        chat_conversations.c.project_id == projects.c.id,
                        chat_conversations.c.user_id == projects.c.user_id,
                    ),
                )
            )
            .where(and_(*conditions))
            .order_by(
                chat_conversations.c.updated_at.desc(),
                chat_conversations.c.id.desc(),
            )
            .limit(limit + 1)
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = _conversation_dict(rows[-1])
            next_cursor = encode_conversation_cursor(last["updated_at"], last["id"])
        return [_conversation_dict(row) for row in rows], next_cursor

    async def list_messages(
        self,
        conversation_id: UUID,
        user_id: str,
        *,
        limit: int,
        before_seq: int | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int | None]:
        conversation = await self.get_conversation(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        conditions = [chat_messages.c.conversation_id == conversation_id]
        if before_seq is not None:
            if before_seq < 1:
                raise ValueError("before_seq 必须是正整数。")
            conditions.append(chat_messages.c.seq < before_seq)
        query = (
            select(chat_messages)
            .where(and_(*conditions))
            .order_by(chat_messages.c.seq.desc())
            .limit(limit + 1)
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        next_before_seq = int(rows[0]["seq"]) if has_more and rows else None
        return conversation, [_message_dict(row) for row in rows], next_before_seq

    async def find_request(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
    ) -> RequestRecord | None:
        conversation = await self.get_conversation(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise ConversationNotFoundError
        query = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.conversation_id == conversation_id,
                    chat_messages.c.request_id == request_id,
                )
            )
            .order_by(chat_messages.c.seq.asc())
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings().all()]
        if not rows:
            return None
        by_role = {str(row["role"]): _message_dict(row) for row in rows}
        user_message = by_role.get("user")
        assistant_message = by_role.get("assistant")
        if user_message is None or assistant_message is None:
            raise DatabaseSchemaError("请求消息对不完整，请检查 chat_messages 数据。")
        return RequestRecord(
            request_id=request_id,
            content=user_message["content"],
            user_message=user_message,
            assistant_message=assistant_message,
        )

    async def create_message_pair(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
        content: str,
    ) -> PreparedMessagePair:
        timestamp = _now()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        async with self.engine.begin() as connection:
            conversation_query = select(chat_conversations).where(
                and_(
                    chat_conversations.c.id == conversation_id,
                    chat_conversations.c.user_id == user_id,
                )
            )
            conversation = (await connection.execute(conversation_query)).mappings().first()
            if conversation is None:
                raise ConversationNotFoundError
            max_seq = await connection.scalar(
                select(func.coalesce(func.max(chat_messages.c.seq), 0)).where(
                    chat_messages.c.conversation_id == conversation_id
                )
            )
            first_seq = int(max_seq or 0) + 1
            title = (
                _conversation_title(content)
                if conversation["title"] == "新会话"
                else conversation["title"]
            )
            await connection.execute(
                insert(chat_messages),
                [
                    {
                        "id": user_message_id,
                        "conversation_id": conversation_id,
                        "seq": first_seq,
                        "request_id": request_id,
                        "role": "user",
                        "content": content,
                        "status": "completed",
                        "display_metadata": {},
                        "error_code": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                    {
                        "id": assistant_message_id,
                        "conversation_id": conversation_id,
                        "seq": first_seq + 1,
                        "request_id": request_id,
                        "role": "assistant",
                        "content": "",
                        "status": "pending",
                        "display_metadata": {},
                        "error_code": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                ],
            )
            await connection.execute(
                update(chat_conversations)
                .where(chat_conversations.c.id == conversation_id)
                .values(title=title, updated_at=timestamp)
            )
        user_message = {
            "id": str(user_message_id),
            "conversation_id": str(conversation_id),
            "seq": first_seq,
            "request_id": request_id,
            "role": "user",
            "content": content,
            "status": "completed",
            "display_metadata": {},
            "error_code": None,
            "created_at": _as_iso(timestamp),
            "updated_at": _as_iso(timestamp),
        }
        assistant_message = {
            **user_message,
            "id": str(assistant_message_id),
            "seq": first_seq + 1,
            "role": "assistant",
            "content": "",
            "status": "pending",
        }
        return PreparedMessagePair(request_id, user_message, assistant_message)

    async def get_incomplete_assistant(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        if (
            await self.get_conversation(conversation_id, user_id)
            is None
        ):
            raise ConversationNotFoundError
        query = (
            select(chat_messages)
            .where(
                and_(
                    chat_messages.c.conversation_id == conversation_id,
                    chat_messages.c.role == "assistant",
                    chat_messages.c.status.in_(["pending", "interrupted"]),
                )
            )
            .order_by(chat_messages.c.seq.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _message_dict(row) if row else None

    async def update_assistant(
        self,
        conversation_id: UUID,
        assistant_message_id: UUID,
        *,
        content: str | None = None,
        status: str,
        display_metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        expected_status: str | Collection[str] | None = None,
    ) -> dict[str, Any]:
        """按预期状态 CAS 更新助手消息，防止旧执行覆盖终态。"""

        values: dict[str, Any] = {"status": status, "updated_at": _now()}
        if content is not None:
            values["content"] = content
        if display_metadata is not None:
            values["display_metadata"] = display_metadata
        values["error_code"] = error_code
        async with self.engine.begin() as connection:
            conditions = [
                chat_messages.c.id == assistant_message_id,
                chat_messages.c.conversation_id == conversation_id,
                chat_messages.c.role == "assistant",
            ]
            if expected_status is not None:
                statuses = (
                    [expected_status]
                    if isinstance(expected_status, str)
                    else list(expected_status)
                )
                if not statuses:
                    raise ValueError("expected_status 不能为空。")
                conditions.append(chat_messages.c.status.in_(statuses))
            result = await connection.execute(
                update(chat_messages)
                .where(and_(*conditions))
                .values(**values)
                .returning(chat_messages)
            )
            row = result.mappings().first()
            if row is None:
                current = await connection.execute(
                    select(chat_messages.c.status).where(
                        and_(
                            chat_messages.c.id == assistant_message_id,
                            chat_messages.c.conversation_id == conversation_id,
                            chat_messages.c.role == "assistant",
                        )
                    )
                )
                current_status = current.scalar()
                if current_status is None:
                    raise ConversationNotFoundError
                if expected_status is not None:
                    raise AssistantStateConflictError(
                        "助手消息状态已变化，旧执行不能覆盖当前状态。"
                    )
                raise ConversationNotFoundError
            await connection.execute(
                update(chat_conversations)
                .where(chat_conversations.c.id == conversation_id)
                .values(updated_at=values["updated_at"])
            )
        return _message_dict(row)

    async def try_advisory_lock(self, conversation_id: UUID) -> AsyncConnection | None:
        """用独立连接持有会话级锁，避免被 checkpoint 连接池复用。"""

        connection = await self.engine.connect()
        lock_key = f"melonclaw:conversation:{conversation_id}"
        try:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:lock_key), 0)"),
                {"lock_key": lock_key},
            )
            acquired = bool(result.scalar())
            await connection.commit()
            if not acquired:
                await connection.close()
                return None
            return connection
        except Exception as exc:
            await connection.rollback()
            await connection.close()
            raise DatabaseUnavailableError("无法获取会话执行锁。") from exc

    async def release_advisory_lock(
        self,
        connection: AsyncConnection,
        conversation_id: UUID,
    ) -> None:
        lock_key = f"melonclaw:conversation:{conversation_id}"
        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:lock_key), 0)"),
                {"lock_key": lock_key},
            )
            await connection.commit()
        finally:
            await connection.close()

    async def try_memory_advisory_lock(
        self,
        lock_key: str,
    ) -> AsyncConnection | None:
        """持有一个跨 Web worker 的 Memory scope/key 协作锁。"""

        connection = await self.engine.connect()
        try:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:lock_key), 1)"),
                {"lock_key": lock_key},
            )
            acquired = bool(result.scalar())
            await connection.commit()
            if not acquired:
                await connection.close()
                return None
            return connection
        except Exception as exc:
            await connection.rollback()
            await connection.close()
            raise DatabaseUnavailableError("无法获取 Memory 写入锁。") from exc

    async def release_memory_advisory_lock(
        self,
        connection: AsyncConnection,
        lock_key: str,
    ) -> None:
        """释放 Memory scope/key 协作锁。"""

        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:lock_key), 1)"),
                {"lock_key": lock_key},
            )
            await connection.commit()
        finally:
            await connection.close()

    async def record_memory_event(
        self,
        *,
        scope_type: str,
        scope_id: str,
        agent_id: str,
        key: str,
        operation: str,
        actor_user_id: str | None,
        tenant_id: str | None,
        request_id: str | None,
        operation_id: str | None = None,
        run_id: str | None,
        version: int,
        content_hash: str | None,
        event_metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """记录 Memory 控制面事件；内容本身仍以 Store 为事实源。"""

        event_id = uuid4()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(memory_events).values(
                    event_id=event_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    agent_id=agent_id,
                    key=key,
                    operation=operation,
                    actor_user_id=actor_user_id,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    operation_id=operation_id,
                    run_id=run_id,
                    version=version,
                    content_hash=content_hash,
                    metadata=event_metadata or {},
                    created_at=_now(),
                )
            )
        return event_id

    async def find_memory_event(
        self,
        *,
        scope_type: str,
        scope_id: str,
        agent_id: str,
        key: str,
        request_id: str,
        operation: str,
        operation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """按操作级 ID 查找已成功记录的 Memory 操作。"""

        conditions = [
            memory_events.c.scope_type == scope_type,
            memory_events.c.scope_id == scope_id,
            memory_events.c.agent_id == agent_id,
            memory_events.c.key == key,
            memory_events.c.request_id == request_id,
            memory_events.c.operation == operation,
        ]
        if operation_id is not None:
            conditions.append(memory_events.c.operation_id == operation_id)
        else:
            conditions.append(memory_events.c.operation_id.is_(None))
        query = (
            select(memory_events)
            .where(and_(*conditions))
            .order_by(memory_events.c.created_at.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return dict(row) if row else None


__all__ = [
    "CHECKPOINT_TABLES",
    "STORE_TABLES",
    "AssistantStateConflictError",
    "BusinessDatabase",
    "ConversationBusyError",
    "ConversationNotFoundError",
    "CONVERSATION_SCHEMA_VERSION",
    "DatabaseConfigurationError",
    "DatabaseSchemaError",
    "DatabaseUnavailableError",
    "PreparedMessagePair",
    "RequestConflictError",
    "RequestRecord",
    "UserContext",
    "chat_conversations",
    "chat_messages",
    "close_memory_store",
    "derive_psycopg_database_url",
    "memory_events",
    "metadata",
    "normalize_async_database_url",
    "open_checkpoint_pool",
    "open_memory_store",
    "schema_migrations",
    "tenants",
    "user_tenants",
    "users",
]
