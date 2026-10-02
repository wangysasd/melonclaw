"""MelonClaw 通用助手的组装入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from melonclaw.backend import build_agent_backend
from melonclaw.core.chat_model import build_chat_model
from melonclaw.core.config import Settings
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS, mcp_interrupts
from melonclaw.core.interpreter import (
    INTERPRETER_MAX_PTC_CALLS,
    INTERPRETER_PTC_TOOLS,
    build_interpreter_middleware,
)
from melonclaw.core.model_catalog import (
    ResolvedModel,
)
from melonclaw.core.prompts import build_system_prompt
from melonclaw.core.user_input import UserInputMiddleware, supports_user_input
from melonclaw.memory import MemoryScopeMiddleware, MemoryService
from melonclaw.middleware import (
    AttachmentHydrationMiddleware,
    AttachmentHydrationProvider,
    FileOperationOrderingMiddleware,
    UserInputGuardMiddleware,
)
from melonclaw.middleware.skill_refresh import SkillRefreshMiddleware
from melonclaw.middleware.tool_name_guard import ToolNameGuardMiddleware
from melonclaw.middleware.tool_selection import CatalogToolSelectorMiddleware
from melonclaw.tool.mcp_install import McpInstallProvider, build_mcp_install_tools
from melonclaw.tool.skill_install import SkillInstallProvider, build_skill_install_tools
from melonclaw.tool.tools import MCP_CATALOG_TOOL_NAME, build_agent_tools

TOOL_NAMES_PREVIEW_LIMIT = 12
MAX_SELECTED_TOOLS_PER_MODEL_CALL = 16


@dataclass(frozen=True)
class AgentContext:
    """单次 Agent 调用的请求上下文，不写入共享全局状态。"""

    user_id: str
    tenant_id: str
    tenant_name: str
    conversation_id: str = ""
    project_id: str = ""
    project_name: str = ""
    workdir_path: str = ""
    agent_id: str = "quickstart-research-agent"
    installation_id: str = "local"
    request_id: str = ""
    run_id: str = ""
    worker_id: str = ""
    tenant_role: str = "member"
    tenant_status: str = "active"
    model_id: str = ""
    model_spec: str = ""
    memory_enabled: bool = False
    memory_admin: bool = False


def _format_tool_summary(tools: list[object]) -> str:
    """生成工具总数和有限预览，避免 Tushare 全量工具刷满日志。"""

    names = [
        getattr(tool, "name", getattr(tool, "__name__", type(tool).__name__))
        for tool in tools
    ]
    if len(names) <= TOOL_NAMES_PREVIEW_LIMIT:
        return ", ".join(names)
    preview = ", ".join(names[:TOOL_NAMES_PREVIEW_LIMIT])
    return f"{len(names)} 个（前 {TOOL_NAMES_PREVIEW_LIMIT} 个：{preview}，...）"


def _tool_name(tool: object) -> str:
    """兼容 LangChain BaseTool 与普通可调用工具的名称。"""

    return getattr(tool, "name", getattr(tool, "__name__", type(tool).__name__))


def _build_tool_selector_middleware(
    model: BaseChatModel,
    tools: list[object],
) -> AgentMiddleware:
    """构造动态工具选择器。

    所有模型都经 ``chat_model.py`` 的 OpenAI 兼容 ``ChatOpenAI`` 适配，目录行的
    ``provider`` 只是展示标签（用户自建行由运营者填写），因此这里不按 provider
    分派，统一使用项目的目录型选择器。
    """

    return CatalogToolSelectorMiddleware(
        model=model,
        catalog_tool_names=[
            _tool_name(tool)
            for tool in tools
            if _tool_name(tool) not in {
                MCP_CATALOG_TOOL_NAME, "prepare_skill_install", "prepare_skill_creation",
                "confirm_skill_install", "prepare_mcp_install", "test_mcp_install", "confirm_mcp_install",
            }
        ],
        max_tools=MAX_SELECTED_TOOLS_PER_MODEL_CALL,
    )


async def build_research_agent(
    settings: Settings,
    *,
    checkpointer: Any | None = None,
    workspace_dir: Path,
    model: ResolvedModel,
    runtime_backend: BackendProtocol | None = None,
    memory_service: MemoryService | None = None,
    attachment_hydration_provider: AttachmentHydrationProvider | None = None,
    skill_install_provider: SkillInstallProvider | None = None,
    mcp_install_provider: McpInstallProvider | None = None,
    client_capabilities: object = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    mcp_tool_allowlists: dict[str, tuple[str, ...]] | None = None,
    skill_dirs: tuple[tuple[str, Path], ...] = (),
) -> CompiledStateGraph:
    """异步发现工具并构建绑定到指定工作区的通用助手。

    Deep Agents 自带文件系统和 task/subagent 能力；这里注入模型、provider
    Tavily 搜索、自定义工具、可选 MCP 工具、通用助手提示词和统一的本地文件后端。

    ``client_capabilities`` 是浏览器声明的能力清单。只有声明 ``user_input_v1``
    的客户端才会拿到 ``ask_user``：旧客户端渲染不出问题卡片，注入只会让它收到
    一张自己看不懂的卡片，进而把会话挂死。
    """

    if checkpointer is None:
        raise RuntimeError(
            "必须传入 PostgreSQL Checkpointer；请先运行 melonclaw-db-init。"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    resolved_model = model
    chat_model: BaseChatModel = build_chat_model(resolved_model)
    tools = await build_agent_tools(
        settings,
        mcp_servers=mcp_servers,
        mcp_tool_allowlists=mcp_tool_allowlists,
    )
    if skill_install_provider is not None:
        tools.extend(build_skill_install_tools(skill_install_provider))
    if mcp_install_provider is not None:
        tools.extend(build_mcp_install_tools(mcp_install_provider))
    resolved_mcp_servers = mcp_servers or {}
    user_input_enabled = supports_user_input(client_capabilities)
    tool_selector = _build_tool_selector_middleware(chat_model, tools)
    interpreter = build_interpreter_middleware()
    backend, skill_sources, skill_permissions = build_agent_backend(
        workspace_dir,
        default_backend=runtime_backend,
        memory_store=memory_service.store if memory_service is not None else None,
        installation_id=(
            memory_service.installation_id
            if memory_service is not None
            else "local"
        ),
        agent_id=(
            memory_service.agent_id
            if memory_service is not None
            else "quickstart-research-agent"
        ),
        skill_dirs=skill_dirs,
    )
    print(
        "运行时后端: CompositeBackend（默认虚拟根目录: "
        f"{workspace_dir}；Skill 路由: "
        f"{', '.join(skill_sources) if skill_sources else '无'}）"
    )
    if skill_sources:
        print(f"已启用 Agent Skill: {', '.join(skill_sources)}")
    print(
        "已启用 QuickJS Interpreter: eval（thread 模式；PTC 只读工具: "
        f"{', '.join(INTERPRETER_PTC_TOOLS)}；每次 eval 最多 "
        f"{INTERPRETER_MAX_PTC_CALLS} 次 PTC 调用）"
    )
    if resolved_mcp_servers:
        server_names = ", ".join(sorted(resolved_mcp_servers))
        print(f"已启用 MCP 服务: {server_names}")
        print(f"已注入 Agent 工具: {_format_tool_summary(tools)}")
        print(
            "每轮主模型动态选择工具上限: "
            f"{MAX_SELECTED_TOOLS_PER_MODEL_CALL}"
        )
        print("动态工具选择器: CatalogToolSelectorMiddleware")

    middleware: list[AgentMiddleware] = [
        SkillRefreshMiddleware(backend, skill_sources),
        ToolNameGuardMiddleware(),
        tool_selector,
        FileOperationOrderingMiddleware(),
        interpreter,
    ]
    if user_input_enabled:
        # 提问工具和它的批次护栏必须成对出现：有工具没护栏，模型就能把
        # ask_user 和副作用工具放在同一批里执行。
        middleware[1:1] = [UserInputMiddleware(), UserInputGuardMiddleware()]
    if memory_service is not None:
        middleware.insert(0, MemoryScopeMiddleware(memory_service))
        print("已启用 Global/Tenant/User Memory（Store 持久化，主 Agent 受控工具）")
    if attachment_hydration_provider is not None:
        middleware.insert(0, AttachmentHydrationMiddleware(attachment_hydration_provider))

    agent = create_deep_agent(
        name="quickstart-research-agent",
        model=chat_model,
        tools=tools,
        middleware=middleware,
        backend=backend,
        skills=skill_sources or None,
        permissions=skill_permissions or None,
        system_prompt=build_system_prompt(
            resolved_mcp_servers,
            memory_enabled=memory_service is not None,
        ),
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=memory_service.store if memory_service is not None else None,
        interrupt_on={**SENSITIVE_TOOL_INTERRUPTS, **mcp_interrupts(tools)},
    )

    agent.melonclaw_mcp_failed = any(
        getattr(tool, "metadata", None) and tool.metadata.get("mcp_failures") for tool in tools
    )
    return agent
