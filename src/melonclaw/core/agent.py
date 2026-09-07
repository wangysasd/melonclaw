"""MelonClaw 通用助手的组装入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from melonclaw.core.config import Settings
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.core.interpreter import (
    INTERPRETER_MAX_PTC_CALLS,
    INTERPRETER_PTC_TOOLS,
    build_interpreter_middleware,
)
from melonclaw.core.memory import MemoryService
from melonclaw.core.model import build_chat_model
from melonclaw.core.prompts import build_system_prompt
from melonclaw.core.skills import build_agent_backend
from melonclaw.middleware import FileOperationOrderingMiddleware
from melonclaw.middleware.memory import MemoryScopeMiddleware
from melonclaw.middleware.tool_selection import CatalogToolSelectorMiddleware
from melonclaw.tool.tools import build_agent_tools

TOOL_NAMES_PREVIEW_LIMIT = 12
MAX_SELECTED_TOOLS_PER_MODEL_CALL = 16
TOOL_SELECTOR_TAG = "tool-selector"


@dataclass(frozen=True)
class AgentContext:
    """单次 Agent 调用的请求上下文，不写入共享全局状态。"""

    user_id: str
    tenant_id: str
    tenant_name: str
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
    memory_enabled: bool = False
    memory_admin: bool = False


def _format_tool_summary(tools: list[object]) -> str:
    """显示工具总数和有限预览，避免 Tushare 全量工具刷满终端。"""

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
    settings: Settings,
    model: BaseChatModel,
    tools: list[object],
) -> AgentMiddleware:
    """按 provider 选择官方或兼容 DeepSeek 的动态工具选择器。"""

    if settings.provider == "openai":
        # 官方 selector 的内部结构化输出也会进入 LangGraph 消息流。只给它使用
        # 的模型副本增加标签，让 streaming.py 隐藏这段内部 JSON；主模型不受影响。
        selector_model = model.model_copy(
            update={
                "tags": [*(getattr(model, "tags", None) or []), TOOL_SELECTOR_TAG],
                "metadata": {
                    **(getattr(model, "metadata", None) or {}),
                    "tool_selector": True,
                },
            }
        )
        return LLMToolSelectorMiddleware(
            model=selector_model,
            max_tools=MAX_SELECTED_TOOLS_PER_MODEL_CALL,
        )

    if settings.provider == "deepseek":
        return CatalogToolSelectorMiddleware(
            model=model,
            catalog_tool_names=[_tool_name(tool) for tool in tools],
            max_tools=MAX_SELECTED_TOOLS_PER_MODEL_CALL,
        )

    raise ValueError(f"没有为 provider={settings.provider!r} 配置工具选择器。")


def _tool_selector_summary(settings: Settings) -> str:
    if settings.provider == "openai":
        return "LangChain 官方 LLMToolSelectorMiddleware"
    return "项目自定义 CatalogToolSelectorMiddleware"


async def build_research_agent(
    settings: Settings,
    *,
    checkpointer: Any | None = None,
    workspace_dir: Path | None = None,
    runtime_backend: BackendProtocol | None = None,
    memory_service: MemoryService | None = None,
) -> CompiledStateGraph:
    """异步发现工具并构建官方 quickstart 形状的通用助手。

    Deep Agents 自带文件系统和 task/subagent 能力；这里注入模型、provider
    Tavily 搜索、自定义工具、可选 MCP 工具、通用助手提示词和统一的本地文件后端。
    """

    if checkpointer is None:
        raise RuntimeError(
            "必须传入 PostgreSQL Checkpointer；请先运行 melonclaw-db-init。"
        )

    backend_root = workspace_dir or settings.runtime_dir
    backend_root.mkdir(parents=True, exist_ok=True)
    model: BaseChatModel = build_chat_model(settings)
    tools = await build_agent_tools(settings)
    tool_selector = _build_tool_selector_middleware(settings, model, tools)
    interpreter = build_interpreter_middleware()
    backend, skill_sources, skill_permissions = build_agent_backend(
        backend_root,
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
    )
    print(
        "运行时后端: CompositeBackend（默认虚拟根目录: "
        f"{backend_root}；/skills/ 直读项目源目录）"
    )
    if skill_sources:
        print(f"已启用 Agent Skill: {', '.join(skill_sources)}")
    print(
        "已启用 QuickJS Interpreter: eval（thread 模式；PTC 只读工具: "
        f"{', '.join(INTERPRETER_PTC_TOOLS)}；每次 eval 最多 "
        f"{INTERPRETER_MAX_PTC_CALLS} 次 PTC 调用）"
    )
    if settings.mcp_servers:
        server_names = ", ".join(sorted(settings.mcp_servers))
        print(f"已启用 MCP 服务: {server_names}")
        print(f"已注入 Agent 工具: {_format_tool_summary(tools)}")
        print(
            "每轮主模型动态选择工具上限: "
            f"{MAX_SELECTED_TOOLS_PER_MODEL_CALL}"
        )
        print(f"动态工具选择器: {_tool_selector_summary(settings)}")

    middleware: list[AgentMiddleware] = [
        tool_selector,
        FileOperationOrderingMiddleware(),
        interpreter,
    ]
    if memory_service is not None:
        middleware.insert(0, MemoryScopeMiddleware(memory_service))
        print("已启用 Global/Tenant/User Memory（Store 持久化，主 Agent 受控工具）")

    return create_deep_agent(
        name="quickstart-research-agent",
        model=model,
        tools=tools,
        middleware=middleware,
        backend=backend,
        skills=skill_sources or None,
        permissions=skill_permissions or None,
        system_prompt=build_system_prompt(
            settings.mcp_servers,
            memory_enabled=memory_service is not None,
        ),
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=memory_service.store if memory_service is not None else None,
        interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
    )
