"""为账户管理与 Cookie 登录添加数据库结构，保留已有用户和租户数据。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.passwords import hash_password


async def _column_length(
    connection: AsyncConnection, table_name: str, column_name: str
) -> int | None:
    result = await connection.execute(
        text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table_name "
            "AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.scalar_one_or_none()


async def _has_column(
    connection: AsyncConnection, table_name: str, column_name: str
) -> bool:
    result = await connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table_name "
            "AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.scalar_one_or_none() is not None


async def upgrade(connection: AsyncConnection) -> None:
    """兼容账户管理上线前的当前 schema；扩列和加列不会删除已有数据。"""

    for table_name in ("tenants", "users"):
        result = await connection.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": table_name},
        )
        if result.scalar_one_or_none() is None:
            raise RuntimeError(
                f"缺少 {table_name} 表；空数据库请先运行 uv run melonclaw-db-init。"
            )

    admin_exists = await connection.execute(
        text("SELECT 1 FROM users WHERE user_id = 'admin'")
    )
    if admin_exists.first() is None:
        raise RuntimeError(
            "现有数据库未找到内置 admin 用户；请检查数据库后再运行增量更新。"
        )

    tenants_name_length = await _column_length(connection, "tenants", "tenant_name_zh")
    if tenants_name_length is not None and tenants_name_length < 64:
        await connection.execute(
            text("ALTER TABLE tenants ALTER COLUMN tenant_name_zh TYPE VARCHAR(64)")
        )

    users_name_length = await _column_length(connection, "users", "user_name_zh")
    if users_name_length is not None and users_name_length < 64:
        await connection.execute(
            text("ALTER TABLE users ALTER COLUMN user_name_zh TYPE VARCHAR(64)")
        )

    if not await _has_column(connection, "tenants", "enabled"):
        await connection.execute(
            text(
                "ALTER TABLE tenants ADD COLUMN enabled BOOLEAN NOT NULL "
                "DEFAULT TRUE"
            )
        )
    if not await _has_column(connection, "tenants", "updated_at"):
        await connection.execute(
            text(
                "ALTER TABLE tenants ADD COLUMN updated_at TIMESTAMPTZ NOT NULL "
                "DEFAULT now()"
            )
        )
    if not await _has_column(connection, "users", "password_hash"):
        await connection.execute(
            text("ALTER TABLE users ADD COLUMN password_hash TEXT")
        )
    if not await _has_column(connection, "users", "updated_at"):
        await connection.execute(
            text(
                "ALTER TABLE users ADD COLUMN updated_at TIMESTAMPTZ NOT NULL "
                "DEFAULT now()"
            )
        )

    invalid_status = await connection.execute(
        text(
            "SELECT 1 FROM users WHERE tenant_status NOT IN ('active', 'deleted') "
            "LIMIT 1"
        )
    )
    if invalid_status.first() is not None:
        raise RuntimeError(
            "users.tenant_status 存在当前账户模型不支持的值，"
            "请先处理这些记录后再运行数据库更新。"
        )

    check_exists = await connection.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = to_regclass('users') AND conname = 'ck_users_status'"
        )
    )
    if check_exists.scalar_one_or_none() is None:
        await connection.execute(
            text(
                "ALTER TABLE users ADD CONSTRAINT ck_users_status "
                "CHECK (tenant_status IN ('active', 'deleted'))"
            )
        )

    auth_sessions_exists = await connection.execute(
        text("SELECT to_regclass('auth_sessions')")
    )
    if auth_sessions_exists.scalar_one_or_none() is not None:
        result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'auth_sessions'"
            )
        )
        session_columns = set(result.scalars().all())
        expected_session_columns = {
            "token_hash", "user_id", "login_user_id", "created_at", "expires_at"
        }
        if not expected_session_columns.issubset(session_columns):
            raise RuntimeError(
                "现有 auth_sessions 表结构不完整；请先检查数据库结构，未写入升级记录。"
            )

    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL
                    REFERENCES users(user_id) ON DELETE RESTRICT,
                login_user_id VARCHAR(64) NOT NULL
                    REFERENCES users(user_id) ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )

    admin_hash = hash_password("admin")
    await connection.execute(
        text(
            "UPDATE users SET password_hash = :password_hash "
            "WHERE user_id = 'admin' AND password_hash IS NULL"
        ),
        {"password_hash": admin_hash},
    )
