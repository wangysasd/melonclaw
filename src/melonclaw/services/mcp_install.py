"""聊天 MCP 同步：字段保留、不可变快照、独立探测与版本化提交。"""

from uuid import UUID, uuid4

from melonclaw.repository.mcp import McpConflictError
from melonclaw.repository.mcp_install import MCP_CONFIG_FIELDS
from melonclaw.services.mcp import McpConfigError
from melonclaw.services.mcp_chat_config import connection_preview
from melonclaw.services.mcp_management import ADMIN_ROLES, McpManagementService


class ChatMcpInstallService:
    def __init__(self, chat):
        self.chat = chat
        self.management = McpManagementService(chat.runtime)

    async def _identity(self, context):
        if context is None or not context.conversation_id:
            raise McpConfigError("MCP 配置需要有效聊天上下文。")
        user = await self.chat.conversations.resolve_user(context.user_id)
        if user.user_id != context.user_id or str(user.tenant_id) != str(context.tenant_id):
            raise McpConfigError("用户归属已变化。")
        storage = self.chat.runtime.require_ready()
        conversation = await storage.get_conversation(UUID(context.conversation_id), user.user_id)
        if conversation is None:
            raise McpConfigError("当前会话不存在或无权访问。")
        project_id = str(conversation["project_id"]) if conversation["project_id"] else ""
        if project_id != context.project_id:
            raise McpConfigError("当前会话的项目归属已变化。")
        if project_id and await storage.get_project(UUID(project_id), user.user_id) is None:
            raise McpConfigError("当前项目不存在或无权访问。")
        return storage, user.user_id, context.conversation_id

    async def _draft(self, context, identifier, installation=None):
        storage, user_id, conversation_id = await self._identity(context)
        draft = await storage.get_mcp_install_draft(identifier, user_id, conversation_id)
        if draft is None:
            raise McpConfigError("MCP 草稿不存在、已过期或不属于当前会话，请重新粘贴配置。")
        if installation is not None and draft["installation"] != installation:
            raise McpConfigError("审批清单已变化，请重新准备。")
        return storage, user_id, conversation_id, draft

    async def prepare(self, context, draft_id, enable=None, tool_allowlist=None, allow_all_tools=False):
        try:
            storage, user_id, _, draft = await self._draft(context, draft_id)
            if draft["installation"] is not None:
                raise McpConfigError("请使用原始配置草稿重新准备，不能复用审批清单作为配置。")
            if ((enable is not None and type(enable) is not bool) or type(allow_all_tools) is not bool
                    or (tool_allowlist is not None and (
                        not isinstance(tool_allowlist, list) or len(tool_allowlist) > 200
                        or any(not isinstance(name, str) or not name.strip() or len(name) > 240 for name in tool_allowlist)
                    )) or (allow_all_tools and tool_allowlist is not None)):
                raise McpConfigError("启用选项或工具白名单无效，解除白名单与指定名单不能同时设置。")
            payload = draft["payload"]
            scope = "global" if await self.management.role(user_id) in ADMIN_ROLES else "user"
            visible = await storage.list_visible_mcp_rows(user_id)
            target = next((item for item in visible if item["scope"] == scope and item["slug"] == payload["slug"]), None)
            if target:
                fields = {key: payload[key] for key in payload["provided_fields"]}
            else:
                fields = {key: value for key, value in payload.items() if key != "provided_fields"}
            for key in ("headers", "env"):
                if key in fields:
                    # 非空对象按键更新；显式空对象清空；省略不改。
                    values = fields[key]
                    removed = [name for name in target[key] if name not in values and name.lower() in {
                        provided.lower() for provided in values
                    }] if target and key == "headers" else []
                    fields[key] = {"set": values, "remove": removed, "clear": not values}
            if tool_allowlist is not None or allow_all_tools:
                fields["tool_allowlist"] = None if allow_all_tools else tool_allowlist
            row = await self.management.prepare(
                user_id, fields, identifier=str(target["id"]) if target else None,
                version=target["version"] if target else None, personal=False,
            )
            if row["transport"] not in {"http", "sse"}:
                raise McpConfigError("聊天配置仅支持 HTTP/SSE，请在连接器页面维护 stdio。")
            row["enabled"] = enable if enable is not None else target["enabled"] if target else True
            snapshot = {key: row[key] for key in MCP_CONFIG_FIELDS}
            changes = ([key for key in MCP_CONFIG_FIELDS if row[key] != target[key]] if target
                       else ["url", "transport", "headers", "enabled", "tool_allowlist"])
            preference = enable if scope == "user" and enable is not None else None
            base_preference = target.get("user_enabled") if target else None
            if target and preference is not None and preference != (base_preference is not False):
                changes.append("personally_enabled")
            operation = "update" if changes and target else "unchanged" if target else "install"
            installation = dict(
                draft_id=str(uuid4()), name=row["slug"], scope=scope, operation=operation,
                target_id=str(target["id"]) if target else None,
                expected_version=target["version"] if target else None, changed_fields=sorted(changes),
                connection=connection_preview(row), transport=row["transport"],
                previous_connection=connection_preview(target) if target and target["url"] else None,
                headers_keys=sorted(row["headers"]), enable=row["enabled"], tool_allowlist=row["tool_allowlist"],
                shadows_global=scope == "user" and any(item["scope"] == "global" and item["slug"] == row["slug"] for item in visible),
            )
            await storage.prepare_mcp_install_draft(draft, installation, {
                "row": snapshot, "tenant_id": str(context.tenant_id), "project_id": context.project_id,
                "preference": preference, "base_preference": base_preference, "connection_status": "untested",
            })
            return {"status": "unchanged" if operation == "unchanged" else "prepared", "installation": installation,
                    "notice": "配置相同，无需保存；如用户明确要求测试，可单独审批测试。" if operation == "unchanged" else
                    "仅准备清单；测试和保存分别审批。未提供字段及个人偏好保持原样，下一条消息生效；未测试不能声称连接可用。"}
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "无法准备 MCP，请核对配置和当前用户状态。"}

    async def _prepared_row(self, context, user_id, draft, installation, *, verify_target=True):
        scope = "global" if await self.management.role(user_id) in ADMIN_ROLES else "user"
        if scope != installation["scope"] or str(context.tenant_id) != draft["payload"]["tenant_id"]:
            raise McpConfigError("当前身份与审批范围不一致，请重新准备。")
        if verify_target and installation["target_id"]:
            await self.management.require(user_id, installation["target_id"], manage=True, version=installation["expected_version"])
        row = draft["payload"]["row"]
        self.management.validate(row)
        if row["scope"] != scope or row["owner_user_id"] != (user_id if scope == "user" else None):
            raise McpConfigError("配置归属与审批范围不一致。")
        return row

    async def test(self, context, installation):
        try:
            storage, user_id, _, draft = await self._draft(context, installation["draft_id"], installation)
            if draft["installed_id"]:
                raise McpConfigError("该清单已提交，请重新准备测试。")
            row = await self._prepared_row(context, user_id, draft, installation)
            result = await self.management._discover_tools(row, refresh=True, cache=False)
            state = "verified" if result["ok"] else "failed"
            await storage.record_mcp_install_test(installation["draft_id"], user_id, context.conversation_id, installation, state)
            return {**result, "connection_status": state}
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "无法测试 MCP，请核对草稿和当前用户状态。"}

    async def confirm(self, context, installation):
        try:
            storage = self.chat.runtime.require_ready()
            async with storage.account_guard():
                storage, user_id, conversation_id, draft = await self._draft(context, installation["draft_id"], installation)
                if draft["installed_id"]:
                    return draft["payload"]["result"]
                row = await self._prepared_row(context, user_id, draft, installation, verify_target=False)
                return await storage.commit_mcp_install(
                    installation["draft_id"], user_id, conversation_id, installation, row,
                )
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "MCP 未确认保存，请在连接器页面核实结果或重新准备。"}
