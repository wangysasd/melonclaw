"""MCP 两层配置和个人偏好；版本比较与写入在同一事务内完成。"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import mcp_servers as servers
from melonclaw.database.schema import mcp_user_preferences as preferences
from melonclaw.repository.mappers import _now


class McpConflictError(ValueError):
    status_code = 409


class McpRepositoryMixin:
    async def list_visible_mcp_rows(self, user_id: str) -> list[dict]:
        """包括停用个人项；同一 SELECT 同时读取配置及个人偏好。"""
        query = (
            select(servers, preferences.c.enabled.label("user_enabled"))
            .outerjoin(
                preferences,
                and_(preferences.c.user_id == user_id, preferences.c.slug == servers.c.slug),
            )
            .where(or_(servers.c.scope == "global", servers.c.owner_user_id == user_id))
            .order_by(servers.c.slug)
        )
        async with self.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(query)).mappings()]

    async def get_mcp_row(self, identifier: str) -> dict | None:
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(servers).where(servers.c.id == UUID(identifier))))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def create_mcp_row(self, **fields) -> dict:
        row = dict(
            fields, id=uuid4(), enabled=False, version=1, created_at=_now(), updated_at=_now()
        )
        try:
            async with self.engine.begin() as connection:
                await connection.execute(insert(servers).values(**row))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise McpConflictError("同一范围内已有此标识的 MCP 服务。") from None
            raise
        return row

    async def update_mcp_row(self, identifier: str, version: int, **fields) -> None:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(servers)
                .where(servers.c.id == UUID(identifier), servers.c.version == version)
                .values(**fields, version=version + 1, updated_at=_now())
            )
            if not result.rowcount:
                raise McpConflictError("配置已变化，请刷新后重试。")

    async def delete_mcp_row(self, identifier: str, version: int) -> None:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                delete(servers).where(
                    servers.c.id == UUID(identifier), servers.c.version == version
                )
            )
            if not result.rowcount:
                raise McpConflictError("配置已变化，请刷新后重试。")

    @staticmethod
    async def _put_preference(connection, user_id: str, slug: str, enabled: bool):
        statement = pg_insert(preferences).values(
            user_id=user_id, slug=slug, enabled=enabled, created_at=_now(), updated_at=_now()
        )
        await connection.execute(
            statement.on_conflict_do_update(
                index_elements=[preferences.c.user_id, preferences.c.slug],
                set_={"enabled": enabled, "updated_at": _now()},
            )
        )

    async def set_mcp_preference(self, user_id: str, slug: str, enabled: bool) -> None:
        async with self.engine.begin() as connection:
            # Lock the visible source while checking visibility and writing preference.
            rows = (
                (
                    await connection.execute(
                        select(servers)
                        .where(
                            servers.c.slug == slug,
                            or_(
                                and_(servers.c.scope == "global", servers.c.enabled.is_(True)),
                                servers.c.owner_user_id == user_id,
                            ),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                raise McpConflictError("服务已不可用，请刷新。")
            await self._put_preference(connection, user_id, slug, enabled)

    async def add_mcp_row(self, user_id: str, identifier: str, version: int) -> None:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(servers)
                        .where(servers.c.id == UUID(identifier), servers.c.version == version)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise McpConflictError("配置已变化，请刷新后重试。")
            if row["scope"] == "global":
                own = (
                    await connection.execute(
                        select(servers.c.id).where(
                            servers.c.slug == row["slug"], servers.c.owner_user_id == user_id
                        )
                    )
                ).first()
                if own or not row["enabled"]:
                    raise McpConflictError("该全局配置已停用或被个人配置替代。")
            else:
                if row["owner_user_id"] != user_id:
                    raise McpConflictError("配置不可用。")
                if not row["enabled"]:
                    await connection.execute(
                        update(servers)
                        .where(servers.c.id == row["id"])
                        .values(enabled=True, version=version + 1, updated_at=_now())
                    )
            await self._put_preference(connection, user_id, row["slug"], True)
