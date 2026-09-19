"""业务 Schema 初始化、历史迁移和完整性检查。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.constants import (
    ATTACHMENT_SCHEMA_VERSION,
    BUSINESS_TABLES,
    CHECKPOINT_TABLES,
    CONVERSATION_SCHEMA_VERSION,
    DEFAULT_PROJECT_NAME,
    DEFAULT_PROJECT_SCHEMA_VERSION,
    MEMORY_SCHEMA_VERSION,
    MODEL_SELECTION_SCHEMA_VERSION,
    MULTITENANT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
    STORE_TABLES,
    USER_INPUT_LIFECYCLE_SCHEMA_VERSION,
    USER_INPUT_SCHEMA_VERSION,
    USER_INPUT_USER_ID_SCHEMA_VERSION,
)
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.schema import (
    chat_attachments,
    chat_conversations,
    chat_message_attachments,
    chat_messages,
    memory_events,
    metadata,
    projects,
    schema_migrations,
    tenants,
    user_interactions,
    user_tenants,
    users,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _default_project_id(user_id: str):
    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:default-project:{user_id}",
    )


class SchemaMigrationMixin:
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
                    tables=[
                        projects,
                        chat_conversations,
                        chat_messages,
                        user_interactions,
                        chat_attachments,
                        chat_message_attachments,
                        memory_events,
                    ],
                )
            )
            await self._migrate_user_interactions_user_id(connection)
            # ``create_all`` 不会给已有 chat_messages 增加新列，显式补齐模型
            # 快照字段以兼容第一阶段上线前创建的数据库。
            await connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
                    "model_id VARCHAR(160)"
                )
            )
            attachment_migration_exists = await connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
                ),
                {"version": ATTACHMENT_SCHEMA_VERSION},
            )
            if not attachment_migration_exists.scalar():
                await connection.execute(
                    insert(schema_migrations).values(
                        version=ATTACHMENT_SCHEMA_VERSION,
                        applied_at=_now(),
                    )
                )
            await connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
                    "model_provider VARCHAR(80)"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
                    "model_name VARCHAR(160)"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
                    "model_display_name VARCHAR(120)"
                )
            )

    @staticmethod
    async def _collapse_duplicate_waiting_interactions(
        connection: AsyncConnection,
    ) -> None:
        """把同一会话里多出来的 waiting 账本收掉，只保留最新的一本。

        账本是审计记录，不能删；这里改成 discarded 并写明原因，既不丢证据，
        也让下面的部分唯一索引能建起来。
        """

        stale = await connection.execute(
            text(
                "SELECT id FROM user_interactions AS interactions "
                "WHERE status = 'waiting' AND id <> ("
                "  SELECT id FROM user_interactions AS latest "
                "  WHERE latest.conversation_id = interactions.conversation_id "
                "  AND latest.status = 'waiting' "
                "  ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1)"
            )
        )
        stale_ids = [row[0] for row in stale]
        if not stale_ids:
            return
        for interaction_id in stale_ids:
            await connection.execute(
                text(
                    "UPDATE user_interactions SET status = 'discarded', "
                    "reason_code = 'duplicate_waiting_archived' WHERE id = :id"
                ),
                {"id": interaction_id},
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

    async def _migrate_user_interactions_user_id(
        self,
        connection: AsyncConnection,
    ) -> None:
        """为已存在的用户问题账本补齐归属用户列。

        ``metadata.create_all`` 只处理不存在的表。用户问题功能早期版本已经
        创建过 ``user_interactions``，但没有 ``user_id``；这里从会话归属回填，
        避免把旧账本删除重建，也让后续服务层的用户隔离查询可以正常工作。
        """

        if not await self._table_exists(connection, "user_interactions"):
            return
        if not await self._column_exists(
            connection,
            table_name="user_interactions",
            column_name="user_id",
        ):
            await connection.execute(
                text(
                    "ALTER TABLE user_interactions "
                    "ADD COLUMN user_id VARCHAR(64)"
                )
            )
        await connection.execute(
            text(
                "UPDATE user_interactions AS interactions "
                "SET user_id = conversations.user_id "
                "FROM chat_conversations AS conversations "
                "WHERE interactions.conversation_id = conversations.id "
                "AND interactions.user_id IS NULL"
            )
        )
        missing_user_id = await connection.execute(
            text(
                "SELECT COUNT(*) FROM user_interactions "
                "WHERE user_id IS NULL"
            )
        )
        if int(missing_user_id.scalar() or 0) > 0:
            raise DatabaseSchemaError(
                "user_interactions 中存在无法匹配会话用户的历史记录，"
                "请先修复 conversation_id 后再初始化数据库。"
            )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ALTER COLUMN user_id SET NOT NULL"
            )
        )
        # 旧版账本没有完整的生命周期字段。每次初始化都使用 IF NOT EXISTS
        # 对齐已有表，避免仅依赖 schema_migrations 记录导致迁移半完成。
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS decision_request_id VARCHAR(36)"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS answer JSONB"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS answer_digest VARCHAR(64)"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS reason_code VARCHAR(80)"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE"
            )
        )
        await connection.execute(
            text(
                "UPDATE user_interactions "
                "SET expires_at = created_at + INTERVAL '1 day' "
                "WHERE expires_at IS NULL"
            )
        )
        missing_expiry = await connection.execute(
            text(
                "SELECT COUNT(*) FROM user_interactions "
                "WHERE expires_at IS NULL"
            )
        )
        if int(missing_expiry.scalar() or 0) > 0:
            raise DatabaseSchemaError(
                "user_interactions 中存在无法计算 expires_at 的历史记录，"
                "请先修复 created_at 后再初始化数据库。"
            )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ALTER COLUMN expires_at SET NOT NULL"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMP WITH TIME ZONE"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE user_interactions "
                "ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE"
            )
        )
        duplicate_interaction = await connection.execute(
            text(
                "SELECT conversation_id, interrupt_id "
                "FROM user_interactions "
                "GROUP BY conversation_id, interrupt_id "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        duplicate_key = duplicate_interaction.first()
        if duplicate_key is not None:
            raise DatabaseSchemaError(
                "user_interactions 中存在重复的 conversation_id + interrupt_id，"
                "请先清理重复记录后再初始化数据库。"
            )
        if not await self._constraint_exists(
            connection,
            "uq_user_interactions_conversation_interrupt",
        ):
            await connection.execute(
                text(
                    "ALTER TABLE user_interactions "
                    "ADD CONSTRAINT uq_user_interactions_conversation_interrupt "
                    "UNIQUE (conversation_id, interrupt_id)"
                )
            )
        # 同一会话最多一张等待中的卡片。部分唯一索引在已有重复 waiting 的数据库上
        # 会创建失败，所以先收掉历史上多出来的那些。
        await self._collapse_duplicate_waiting_interactions(connection)
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_interactions_active "
                "ON user_interactions (conversation_id) WHERE status = 'waiting'"
            )
        )
        migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": USER_INPUT_USER_ID_SCHEMA_VERSION},
        )
        if not migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=USER_INPUT_USER_ID_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )
        lifecycle_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": USER_INPUT_LIFECYCLE_SCHEMA_VERSION},
        )
        if not lifecycle_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=USER_INPUT_LIFECYCLE_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

    @staticmethod
    async def _ensure_default_projects(connection: AsyncConnection) -> None:
        """为每个已有用户补齐唯一、可重复初始化的默认 Project。"""

        result = await connection.execute(
            text("SELECT user_id FROM users ORDER BY user_id")
        )
        user_ids = [str(user_id) for user_id in result.scalars().all()]
        timestamp = _now()
        for user_id in user_ids:
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

        model_selection_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": MODEL_SELECTION_SCHEMA_VERSION},
        )
        if not model_selection_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=MODEL_SELECTION_SCHEMA_VERSION,
                    applied_at=_now(),
                )
            )

        user_input_migration_exists = await connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"
            ),
            {"version": USER_INPUT_SCHEMA_VERSION},
        )
        if not user_input_migration_exists.scalar():
            await connection.execute(
                insert(schema_migrations).values(
                    version=USER_INPUT_SCHEMA_VERSION,
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
