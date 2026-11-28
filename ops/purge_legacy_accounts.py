"""正式运营前一次性清理旧账户。操作步骤见 docs/deployment.md。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.config import load_settings
from melonclaw.database import BUSINESS_TABLES, Database, DatabaseSchemaError

OLD_USERS = "SELECT delete_user.user_id FROM users AS delete_user WHERE delete_user.user_id <> 'admin'"
OLD_TENANTS = (
    "SELECT delete_tenant.tenant_id FROM tenants AS delete_tenant WHERE delete_tenant.tenant_id <> 'system'"
)
OLD_CONVERSATIONS = f"SELECT id FROM chat_conversations WHERE user_id IN ({OLD_USERS})"
OLD_BASES = f"SELECT id FROM knowledge_bases WHERE owner_user_id IN ({OLD_USERS})"
OLD_JOBS = f"kb_id IN ({OLD_BASES}) OR requested_by IN ({OLD_USERS})"
OLD_TASK = f"owner_user_id IN ({OLD_USERS}) OR tenant_id IN ({OLD_TENANTS})"
OLD_REPORTS = f"SELECT id FROM reports WHERE submitter_user_id IN ({OLD_USERS})"
OLD_PHONES = f"SELECT phone_hmac FROM user_phones WHERE user_id IN ({OLD_USERS})"
OLD_SKILLS = f"SELECT id FROM skills WHERE scope = 'user' AND created_by IN ({OLD_USERS})"

# Preserve the IDs until Checkpointer and other tables without ownership FKs are handled.
OLD_THREADS = f"""
    SELECT CAST(id AS text) FROM chat_conversations WHERE user_id IN ({OLD_USERS})
    UNION ALL
    SELECT execution_context_id FROM task_runs WHERE {OLD_TASK}
"""
OLD_MEMORY = f"""
    (prefix LIKE 'melonclaw.memory.user.%' AND EXISTS (
        SELECT 1 FROM ({OLD_USERS}) AS owner
        WHERE right(prefix, length(owner.user_id) + 1) = '.' || owner.user_id
    )) OR (prefix LIKE 'melonclaw.memory.tenant.%' AND EXISTS (
        SELECT 1 FROM ({OLD_TENANTS}) AS owner
        WHERE right(prefix, length(owner.tenant_id) + 1) = '.' || owner.tenant_id
    ))
"""


@dataclass(frozen=True)
class Step:
    table: str
    where: str
    set_columns: str = ""


# Explicitly remove FK dependants, including CASCADE rows, so the read-only
# preview can count them. Each filter's source rows survive until it has run.
STEPS = (
    *(
        Step(table, f"thread_id IN ({OLD_THREADS})")
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
    ),
    Step("store", OLD_MEMORY),
    Step("memory_events", f"""
        actor_user_id IN ({OLD_USERS}) OR tenant_id IN ({OLD_TENANTS})
        OR (scope_type = 'user' AND EXISTS (
            SELECT 1 FROM ({OLD_USERS}) AS owner
            WHERE right(scope_id, length(owner.user_id) + 1) = ':' || owner.user_id
        )) OR (scope_type = 'tenant' AND EXISTS (
            SELECT 1 FROM ({OLD_TENANTS}) AS owner
            WHERE right(scope_id, length(owner.tenant_id) + 1) = ':' || owner.tenant_id
        ))
    """),
    Step("report_events", f"report_id IN ({OLD_REPORTS})"),
    Step("reports", f"submitter_user_id IN ({OLD_USERS})"),
    Step("task_runs", OLD_TASK),
    Step("task_operations", OLD_TASK),
    Step("tasks", OLD_TASK),
    Step("user_interactions", f"user_id IN ({OLD_USERS}) OR conversation_id IN ({OLD_CONVERSATIONS})"),
    Step("conversation_artifacts", f"""
        conversation_id IN ({OLD_CONVERSATIONS})
        OR message_id IN (SELECT id FROM chat_messages WHERE conversation_id IN ({OLD_CONVERSATIONS}))
    """),
    Step("chat_message_attachments", f"""
        message_id IN (SELECT id FROM chat_messages WHERE conversation_id IN ({OLD_CONVERSATIONS}))
        OR attachment_id IN (SELECT id FROM chat_attachments WHERE user_id IN ({OLD_USERS}))
    """),
    Step("chat_messages", f"conversation_id IN ({OLD_CONVERSATIONS})"),
    Step("chat_attachments", f"user_id IN ({OLD_USERS})"),
    Step("environment_operations", f"user_id IN ({OLD_USERS})"),
    Step("mcp_install_drafts", f"user_id IN ({OLD_USERS}) OR conversation_id IN ({OLD_CONVERSATIONS})"),
    Step("chat_conversations", f"user_id IN ({OLD_USERS})"),
    Step("projects", f"user_id IN ({OLD_USERS})"),
    # Break the knowledge pipeline/document cycles before deleting dependent rows.
    Step("knowledge_bases", f"active_pipeline_job_id IN (SELECT id FROM knowledge_jobs WHERE {OLD_JOBS})",
         "active_pipeline_job_id = NULL"),
    Step("knowledge_documents", f"kb_id IN ({OLD_BASES}) AND active_revision_id IS NOT NULL",
         "active_revision_id = NULL"),
    Step("knowledge_jobs", OLD_JOBS),
    Step("knowledge_events", f"kb_id IN ({OLD_BASES})"),
    Step("knowledge_events", f"kb_id NOT IN ({OLD_BASES}) AND actor_user_id IN ({OLD_USERS})",
         "actor_user_id = NULL"),
    Step("knowledge_members", f"kb_id IN ({OLD_BASES}) OR user_id IN ({OLD_USERS})"),
    Step("knowledge_members", f"""
        kb_id NOT IN ({OLD_BASES}) AND user_id NOT IN ({OLD_USERS}) AND granted_by IN ({OLD_USERS})
    """, "granted_by = 'admin'"),
    Step("knowledge_chunks", f"kb_id IN ({OLD_BASES})"),
    Step("knowledge_document_texts", f"kb_id IN ({OLD_BASES})"),
    Step("knowledge_document_revisions", f"kb_id IN ({OLD_BASES})"),
    Step("knowledge_documents", f"kb_id IN ({OLD_BASES})"),
    Step("knowledge_documents", f"kb_id NOT IN ({OLD_BASES}) AND created_by IN ({OLD_USERS})",
         "created_by = 'admin'"),
    Step("knowledge_bases", f"owner_user_id IN ({OLD_USERS})"),
    Step("skill_user_states", f"user_id IN ({OLD_USERS}) OR skill_id IN ({OLD_SKILLS})"),
    Step("mcp_user_preferences", f"user_id IN ({OLD_USERS})"),
    Step("provider_user_keys", f"user_id IN ({OLD_USERS})"),
    Step("mcp_servers", f"scope = 'user' AND owner_user_id IN ({OLD_USERS})"),
    Step("environment_variables", f"scope = 'user' AND owner_user_id IN ({OLD_USERS})"),
    Step("skills", f"id IN ({OLD_SKILLS})"),
    Step("model_configs", f"scope = 'user' AND created_by IN ({OLD_USERS})"),
    *(
        Step(table, f"scope = 'global' AND created_by IN ({OLD_USERS})", "created_by = 'admin'")
        for table in ("skills", "model_configs", "model_providers")
    ),
    *(
        Step(table, f"created_by IN ({OLD_USERS}) AND (scope = 'global' OR owner_user_id = 'admin')",
             "created_by = 'admin'")
        for table in ("mcp_servers", "environment_variables")
    ),
    Step("auth_sessions", f"user_id IN ({OLD_USERS}) OR login_user_id IN ({OLD_USERS})"),
    Step("registration_verifications", f"user_id IN ({OLD_USERS})"),
    Step("sms_challenges", f"user_id IN ({OLD_USERS}) OR phone_hmac IN ({OLD_PHONES})"),
    Step("sms_quotas", f"""
        (bucket LIKE 'gap:%' AND split_part(bucket, ':', 2) IN ({OLD_PHONES}))
        OR (bucket LIKE 'day:phone:%' AND split_part(bucket, ':', 3) IN ({OLD_PHONES}))
    """),
    Step("user_phones", f"user_id IN ({OLD_USERS})"),
    Step("users", "user_id <> 'admin'"),
    Step("tenants", "tenant_id <> 'system'"),
)

LOCK_TABLES = ", ".join(sorted(set(BUSINESS_TABLES) | {step.table for step in STEPS}))


class PurgeBlocked(Exception):
    """An unmet precondition with a message safe to display."""


async def check_admin(connection: AsyncConnection) -> None:
    valid = await connection.scalar(text("""
        SELECT EXISTS (
            SELECT 1 FROM users JOIN tenants USING (tenant_id)
            WHERE user_id = 'admin' AND tenant_id = 'system'
              AND tenant_status = 'active' AND enabled IS TRUE
        )
    """))
    if not valid:
        raise PurgeBlocked("未找到启用的 system 租户及其有效 admin 管理员。")


async def purge(database: Database, expected_database: str | None) -> None:
    apply = expected_database is not None
    async with database.engine.begin() as connection:
        if not apply:
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await connection.execute(text("SET LOCAL search_path = public, pg_catalog"))
        database_name = await connection.scalar(text("SELECT current_database()"))
        if apply:
            if database_name != expected_database:
                raise PurgeBlocked("数据库名称与 --expect-database 不一致。")
            # The built-in task executor holds the same lock for the Web lifetime.
            acquired = await connection.scalar(text(
                "SELECT pg_try_advisory_xact_lock(hashtext(current_schema()), 741925)"
            ))
            if not acquired:
                raise PurgeBlocked("Web 任务执行器仍持有锁，请先停止服务。")
            await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            await connection.execute(text(f"LOCK TABLE {LOCK_TABLES} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
        else:
            print(f"只读预览 · 数据库 {database_name!r} · public")

        await check_admin(connection)
        pending = await connection.scalar(text(f"""
            SELECT count(*) FROM knowledge_jobs
            WHERE kb_id NOT IN ({OLD_BASES}) AND requested_by IN ({OLD_USERS})
              AND status IN ('queued', 'running')
        """))
        if pending:
            raise PurgeBlocked(f"保留的知识库有 {pending} 个由待删除用户发起的活跃作业，请先结束这些作业。")

        counts = []
        for step in STEPS:
            action = "更新" if step.set_columns else "删除"
            if not apply:
                count = await connection.scalar(text(f"SELECT count(*) FROM {step.table} WHERE {step.where}"))
            else:
                statement = (
                    f"UPDATE {step.table} SET {step.set_columns}"
                    if step.set_columns else f"DELETE FROM {step.table}"
                )
                try:
                    result = await connection.execute(text(f"{statement} WHERE {step.where}"))
                except DBAPIError as exc:
                    state = getattr(exc.orig, "sqlstate", None) or "未知"
                    raise PurgeBlocked(f"{action} {step.table} 失败（SQLSTATE {state}），事务已回滚。") from exc
                count = result.rowcount
            if count or step.table in {"users", "tenants"}:
                counts.append((action, step.table, count))

        if apply:
            await check_admin(connection)
            remaining = await connection.scalar(text("""
                SELECT (SELECT count(*) FROM users) = 1 AND (SELECT count(*) FROM tenants) = 1
            """))
            if not remaining:
                raise PurgeBlocked("清理后的用户或租户数量异常，已回滚。")

    for action, table, count in counts:
        print(f"{action} {table} {count} 行")
    if apply:
        print("已提交。数据库用户仅剩 admin，租户仅剩 system。")
    else:
        print("预览完成，数据库没有写入。")


async def run(expected_database: str | None) -> None:
    database = Database(load_settings().database_url)
    try:
        await database.open()
        await database.verify_schema(require_checkpointer=True, require_store=True)
        await purge(database, expected_database)
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="停服和备份后提交清理")
    parser.add_argument("--expect-database", help="与预览结果一致的 PostgreSQL 数据库名")
    args = parser.parse_args()
    if args.apply != (args.expect_database is not None):
        parser.error("--apply 和 --expect-database 必须一起使用。")
    try:
        asyncio.run(run(args.expect_database))
    except (PurgeBlocked, DatabaseSchemaError) as exc:
        print(f"清理停止。{exc}", file=sys.stderr)
        return 1
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None) or "未知"
        print(f"数据库操作失败（SQLSTATE {state}）。请核对连接、权限或约束后重新预览。", file=sys.stderr)
        return 1
    except Exception as exc:
        # Connection and driver errors can include credentials or row values.
        print(f"清理停止（{type(exc).__name__}）。请核对部署配置和 PostgreSQL 连接。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
