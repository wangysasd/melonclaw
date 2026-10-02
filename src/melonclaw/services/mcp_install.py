"""聊天 MCP 安装编排；复用管理校验与探测，身份来自受控上下文。"""

from uuid import UUID, uuid4

from melonclaw.repository.mcp import McpConflictError
from melonclaw.services.mcp import McpConfigError
from melonclaw.services.mcp_chat_config import connection_preview
from melonclaw.services.mcp_management import McpManagementService


class ChatMcpInstallService:
    def __init__(self, chat):
        self.chat = chat
        self.management = McpManagementService(chat.runtime)

    async def _identity(self, context):
        if context is None or not context.conversation_id:
            raise McpConfigError("MCP 安装需要有效聊天上下文。")
        user = await self.chat.conversations.resolve_user(context.user_id)
        if str(user.tenant_id) != str(context.tenant_id):
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

    async def _row(self, user_id, payload):
        fields = {**payload, "headers": {"set": payload["headers"], "remove": [], "clear": False},
                  "env": {"set": payload["env"], "remove": [], "clear": False}}
        return await self.management.prepare(user_id, fields, personal=True)

    async def prepare(self, context, draft_id, enable=True, tool_allowlist=None):
        try:
            storage, user_id, _, draft = await self._draft(context, draft_id)
            if not draft["payload"]:
                raise McpConfigError("该草稿已安装，请重新粘贴配置。")
            if not isinstance(enable, bool) or (tool_allowlist is not None and (
                not isinstance(tool_allowlist, list) or len(tool_allowlist) > 200
                or any(not isinstance(name, str) or not name.strip() or len(name) > 240 for name in tool_allowlist)
            )):
                raise McpConfigError("启用选项或工具白名单无效。")
            payload = {**draft["payload"], "tool_allowlist": tool_allowlist}
            await self._row(user_id, payload)
            visible = await storage.list_visible_mcp_rows(user_id)
            if any(row["scope"] == "user" and row["slug"] == payload["slug"] for row in visible):
                raise McpConfigError("已有同名个人 MCP，请在连接器页面编辑；聊天不会自动覆盖。")
            installation = dict(
                draft_id=str(uuid4()), name=payload["slug"], scope="user",
                connection=connection_preview(payload), transport=payload["transport"],
                headers_keys=sorted(payload["headers"]), enable=enable, tool_allowlist=tool_allowlist,
                shadows_global=any(row["scope"] == "global" and row["slug"] == payload["slug"] for row in visible),
            )
            await storage.prepare_mcp_install_draft(draft, installation, payload)
            return {"status": "prepared", "installation": installation,
                    "notice": "仅准备清单；测试会发送凭据，须单独审批。安装仅影响自己，下一条消息生效。"}
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "无法准备 MCP，请核对配置和当前用户状态。"}

    async def test(self, context, installation):
        try:
            _, user_id, _, draft = await self._draft(context, installation["draft_id"], installation)
            if draft["installed_id"]:
                raise McpConfigError("该清单已安装，请在连接器页面测试。")
            row = await self._row(user_id, draft["payload"])
            return await self.management._discover_tools(row, refresh=True, cache=False)
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "无法测试 MCP，请核对草稿和当前用户状态。"}

    async def confirm(self, context, installation):
        try:
            storage, user_id, conversation_id, draft = await self._draft(context, installation["draft_id"], installation)
            if draft["installed_id"]:
                identifier = str(draft["installed_id"])
            else:
                row = await self._row(user_id, draft["payload"])
                identifier = await storage.commit_mcp_install(
                    installation["draft_id"], user_id, conversation_id, installation, row,
                )
            return {"status": "installed", "id": identifier, "scope": "user", "enabled": installation["enable"],
                    "message": "MCP 已安装到你名下，下一条消息可用。" if installation["enable"] else "MCP 已安装到你名下，尚未启用。"}
        except (McpConfigError, McpConflictError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception:
            return {"status": "error", "message": "MCP 安装未确认成功，请在连接器页面核实结果；同名配置不会覆盖。"}
