"""业务操作使用的 PostgreSQL Advisory Lock。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.errors import DatabaseUnavailableError


class ConcurrencyMixin:
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


