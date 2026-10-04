"""MelonClaw 通用助手的组装入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from melonclaw.backend import build_agent_backend
from melonclaw.core.agent_controls import build_agent_controls
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
from melonclaw.core.tool_catalog import FIND_TOOLS_NAME
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
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.middleware.usage_observation import UsageObservationMiddleware
from melonclaw.tool.mcp_install import McpInstallProvider, build_mcp_install_tools
from melonclaw.tool.skill_install import SkillInstallProvider, build_skill_install_tools
from melonclaw.tool.tool_discovery import build_tool_discovery
from melonclaw.tool.tools import MCP_CATALOG_TOOL_NAME, build_agent_tools

TOOL_NAMES_PREVIEW_LIMIT = 12


@dataclass(frozen=True)
class AgentContext:
    """单次 Agent 调用的请求上下文，不写入共享全局状态。"""

    user_id: str
    tenant_id: str
    tenant_name: str
    user_message_id: str = ""
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
    settings: Settings,
) -> ToolPoolMiddleware:
    """组装官方选择器的按需生命周期，仅索引已授权的应用工具。"""

    return ToolPoolMiddleware(
        model=model,
        catalog_tools=[
            tool
            for tool in tools
            if _tool_name(tool) not in {
                FIND_TOOLS_NAME, MCP_CATALOG_TOOL_NAME, "prepare_skill_install", "prepare_skill_creation",
                "confirm_skill_install", "prepare_mcp_install", "test_mcp_install", "confirm_mcp_install",
            }
        ],
        pool_size=settings.tool_pool_size,
        selection_size=settings.tool_selection_size,
        max_requests=settings.tool_selection_max_requests,
        timeout_seconds=settings.tool_selection_timeout_seconds,
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
    settings.summary_trigger_tokens(resolved_model.context_window)
    chat_model: BaseChatModel = build_chat_model(resolved_model, output_reserve=settings.agent_output_reserve)
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
    tools.append(build_tool_discovery(settings.tool_selection_max_requests))
    tool_selector = _build_tool_selector_middleware(chat_model, tools, settings)
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
            "会话业务工具池上限: "
            f"{settings.tool_pool_size}"
        )
        print("动态工具选择器: ToolPoolMiddleware")

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

    middleware = [*build_agent_controls(
        chat_model, backend, settings, context_window=resolved_model.context_window,
        tool_pool=tool_selector,
    ), *middleware]
    middleware = [m for m in middleware if not isinstance(m, UsageObservationMiddleware)] + [
        m for m in middleware if isinstance(m, UsageObservationMiddleware)
    ]
    child_controls = build_agent_controls(
        chat_model, backend, settings, context_window=resolved_model.context_window,
        scope="subagent", tool_pool=tool_selector,
    )
    child_middleware = [m for m in child_controls if not isinstance(m, UsageObservationMiddleware)] + [
        tool_selector, *[m for m in child_controls if isinstance(m, UsageObservationMiddleware)]
    ]
    agent = create_deep_agent(
        name="quickstart-research-agent",
        model=chat_model,
        tools=tools,
        middleware=middleware,
        subagents=[{
            **GENERAL_PURPOSE_SUBAGENT,
            "tools": tools,
            "middleware": child_middleware,
            "skills": skill_sources or None,
            "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"] + (
                "\n优先使用当前工具；缺少能力时调用 find_tools 描述具体需求，下一轮使用新增工具。"
            ),
        }],
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

    agent.melonclaw_usage_enabled = settings.agent_usage_enabled
    agent.melonclaw_mcp_failed = any(
        getattr(tool, "metadata", None) and tool.metadata.get("mcp_failures") for tool in tools
    )
    return agent
