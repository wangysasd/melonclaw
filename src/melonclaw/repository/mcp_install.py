"""MCP 聊天草稿与幂等角色范围安装；安装、偏好和结果在同一事务提交。"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import chat_conversations as conversations
from melonclaw.database.schema import mcp_install_drafts as drafts
from melonclaw.database.schema import mcp_servers as servers
from melonclaw.database.schema import mcp_user_preferences as preferences
from melonclaw.database.schema import tenants, users
from melonclaw.repository.mappers import _now
from melonclaw.repository.mcp import McpConflictError
from melonclaw.repository.resource_versions import resource_change

MCP_CONFIG_FIELDS = (
    "slug", "display_name", "description", "scope", "owner_user_id", "created_by",
    "transport", "url", "command", "args", "headers", "env", "tool_allowlist", "enabled",
)
MCP_UPDATE_FIELDS = tuple(key for key in MCP_CONFIG_FIELDS if key not in {
    "slug", "scope", "owner_user_id", "created_by",
})


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

    async def record_mcp_install_test(self, identifier, user_id, conversation_id, installation, state):
        if state not in {"verified", "failed"}:
            raise McpConflictError("测试状态无效。")
        async with self.engine.begin() as connection:
            draft = (await connection.execute(select(drafts).where(
                drafts.c.id == UUID(identifier), drafts.c.user_id == user_id,
                drafts.c.conversation_id == UUID(str(conversation_id)), drafts.c.expires_at > _now(),
            ).with_for_update())).mappings().first()
            if draft is None or draft["installed_id"] or draft["installation"] != installation:
                raise McpConflictError("测试清单已提交或失效，请重新准备。")
            await connection.execute(update(drafts).where(drafts.c.id == draft["id"]).values(
                payload={**draft["payload"], "connection_status": state},
            ))

    @resource_change(payload="row")
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
                    return draft["payload"]["result"]
                payload = draft["payload"]
                if ({key: row[key] for key in MCP_CONFIG_FIELDS} != payload["row"]
                        or row["scope"] != installation["scope"]
                        or row["slug"] != installation["name"]
                        or row["owner_user_id"] != (user_id if row["scope"] == "user" else None)):
                    raise McpConflictError("配置与审批快照不一致。")
                conversation = (await connection.execute(select(conversations).where(
                    conversations.c.id == UUID(str(conversation_id)), conversations.c.user_id == user_id,
                ).with_for_update())).mappings().first()
                if conversation is None or str(conversation["project_id"] or "") != payload["project_id"]:
                    raise McpConflictError("会话归属已变化，请重新准备。")
                identity = (await connection.execute(select(users.c.tenant_id, users.c.tenant_role).join(
                    tenants, tenants.c.tenant_id == users.c.tenant_id,
                ).where(users.c.user_id == user_id, users.c.tenant_status == "active", tenants.c.enabled.is_(True)))).mappings().first()
                scope = "global" if identity and identity["tenant_role"] in {"admin", "owner"} else "user"
                if identity is None or identity["tenant_id"] != payload["tenant_id"] or scope != row["scope"]:
                    raise McpConflictError("身份或权限已变化，请重新准备。")
                now = _now()
                if installation["target_id"]:
                    target = (await connection.execute(select(servers).where(
                        servers.c.id == UUID(installation["target_id"]),
                    ).with_for_update())).mappings().first()
                    if (target is None or target["version"] != installation["expected_version"]
                            or any(target[key] != row[key] for key in ("slug", "scope", "owner_user_id", "created_by"))):
                        raise McpConflictError("MCP 已变化或删除，请重新准备，不能覆盖新配置。")
                    identifier = target["id"]
                    if payload["preference"] is not None:
                        preference = await connection.scalar(select(preferences.c.enabled).where(
                            preferences.c.user_id == user_id, preferences.c.slug == row["slug"],
                        ))
                        if preference != payload["base_preference"]:
                            raise McpConflictError("个人使用偏好已变化，请重新准备。")
                    changed = installation["operation"] != "unchanged"
                    version = target["version"] + int(changed)
                    if changed:
                        await connection.execute(update(servers).where(servers.c.id == identifier).values(
                            **{key: row[key] for key in MCP_UPDATE_FIELDS}, version=version, updated_at=now,
                        ))
                else:
                    identifier, version = uuid4(), 1
                    await connection.execute(insert(servers).values(
                        **row, id=identifier, version=version, created_at=now, updated_at=now,
                    ))
                if row["scope"] == "user" and (not installation["target_id"] or payload["preference"] is not None):
                    await self._put_preference(connection, user_id, row["slug"], row["enabled"])
                state = payload["connection_status"]
                status = {"install": "installed", "update": "updated", "unchanged": "unchanged"}[installation["operation"]]
                message = "MCP 已保存，后续消息按启用状态和个人偏好使用新配置。" if status != "unchanged" else "配置相同，无需更新。"
                message += {"verified": "该快照连接测试通过。", "failed": "连接测试失败，已按审批保存，连接未验证。", "untested": "尚未验证连接。"}[state]
                result = {"status": status, "id": str(identifier), "scope": row["scope"],
                          "enabled": row["enabled"], "version": version, "connection_status": state, "message": message}
                await connection.execute(update(drafts).where(drafts.c.id == draft["id"]).values(
                    installed_id=identifier, payload={"result": result},
                ))
                return result
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise McpConflictError("同名 MCP 已被创建，请重新准备并核对更新预览。") from None
            raise
