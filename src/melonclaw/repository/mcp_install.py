"""MCP 聊天草稿与幂等个人安装；安装、偏好和结果在同一事务提交。"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from melonclaw.core.mcp_credentials import decode_credentials
from melonclaw.database.schema import mcp_install_drafts as drafts
from melonclaw.database.schema import mcp_servers as servers
from melonclaw.repository.mappers import _now
from melonclaw.repository.mcp import McpConflictError


class McpInstallRepositoryMixin:
    async def stage_mcp_drafts(self, user_id, conversation_id, items):
        if not items:
            return
        now = _now()
        async with self.engine.begin() as connection:
            # 只清理已过期草稿，凭据不无限期保留。
            await connection.execute(delete(drafts).where(drafts.c.expires_at <= now))
            for item in items:
                await connection.execute(pg_insert(drafts).values(
                    id=UUID(item["id"]), user_id=user_id, conversation_id=UUID(str(conversation_id)),
                    payload=item["payload"], created_at=now, expires_at=now + timedelta(hours=24),
                ).on_conflict_do_nothing(index_elements=[drafts.c.id]))

    async def get_mcp_install_draft(self, identifier, user_id, conversation_id):
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(drafts).where(
                drafts.c.id == UUID(identifier), drafts.c.user_id == user_id,
                drafts.c.conversation_id == UUID(str(conversation_id)), drafts.c.expires_at > _now(),
            ))).mappings().first()
            return dict(row) if row else None

    async def prepare_mcp_install_draft(self, source, installation, payload):
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(insert(drafts).values(
                id=UUID(installation["draft_id"]), user_id=source["user_id"],
                conversation_id=source["conversation_id"], payload=payload,
                installation=installation, created_at=now, expires_at=source["expires_at"],
            ))

    async def commit_mcp_install(self, identifier, user_id, conversation_id, installation, row):
        try:
            async with self.engine.begin() as connection:
                draft = (await connection.execute(select(drafts).where(
                    drafts.c.id == UUID(identifier), drafts.c.user_id == user_id,
                    drafts.c.conversation_id == UUID(str(conversation_id)),
                    drafts.c.expires_at > _now(),
                ).with_for_update())).mappings().first()
                if draft is None or draft["installation"] != installation:
                    raise McpConflictError("草稿已过期或审批清单已变化，请重新准备。")
                if draft["installed_id"]:
                    return str(draft["installed_id"])
                # 检查要提交的连接仍与审批草稿一致，防止其他入口替换连接。
                expected = {key: row[key] for key in draft["payload"]}
                expected.update(headers=decode_credentials(row["headers"]), env=decode_credentials(row["env"]))
                if expected != draft["payload"] or row["scope"] != "user" or row["owner_user_id"] != user_id:
                    raise McpConflictError("安装配置与审批草稿不一致。")
                identifier = uuid4()
                now = _now()
                await connection.execute(insert(servers).values(
                    **row, id=identifier, enabled=installation["enable"], version=1,
                    created_at=now, updated_at=now,
                ))
                await self._put_preference(connection, user_id, row["slug"], installation["enable"])
                await connection.execute(update(drafts).where(drafts.c.id == draft["id"]).values(
                    installed_id=identifier, payload={},
                ))
                return str(identifier)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise McpConflictError("已有同名个人 MCP，请到连接器页面编辑；聊天不会自动覆盖。") from None
            raise
