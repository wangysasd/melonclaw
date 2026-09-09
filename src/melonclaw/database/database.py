"""数据库基础设施门面，管理 Engine 和 Schema 生命周期。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.errors import DatabaseUnavailableError
from melonclaw.database.migrations import SchemaMigrationMixin


class Database(SchemaMigrationMixin):
    """使用 SQLAlchemy AsyncEngine 提供数据库基础能力。"""

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
