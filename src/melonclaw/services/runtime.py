"""应用运行时资源和 Agent 实例管理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from melonclaw.backend import project_skills_enabled
from melonclaw.core.agent import build_research_agent
from melonclaw.core.config import Settings, load_settings
from melonclaw.database import (
    Database,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
    close_memory_store,
    open_checkpoint_pool,
    open_memory_store,
)
from melonclaw.memory import MemoryService
from melonclaw.output.formatting import sanitize_text
from melonclaw.repository import BusinessRepository


@dataclass
class ChatRuntime:
    """应用运行时共享的数据库、Agent 和 Memory 资源。"""

    settings: Settings | None = None
    database: Database | None = None
    storage: BusinessRepository | None = None
    checkpoint_pool: AsyncConnectionPool | None = None
    checkpointer: AsyncPostgresSaver | None = None
    memory_store_context: Any | None = None
    memory_store: Any | None = None
    memory_service: MemoryService | None = None
    project_agents: dict[str, Any] | None = None
    startup_error: str | None = None
    worker_id: str = field(default_factory=lambda: f"web-{uuid4()}")

    async def initialize(self) -> None:
        """打开连接池并校验数据库、Memory 和模型配置。"""

        try:
            self.settings = load_settings()
            self.settings.validate()
            self.database = Database(self.settings.database_url)
            await self.database.open()
            self.storage = BusinessRepository(self.database)
            self.memory_store_context, self.memory_store = await open_memory_store(
                self.settings.database_url
            )
            # 初始化和迁移由 melonclaw-db-init 独立执行，服务启动只检查状态。
            await self.database.verify_schema(
                require_checkpointer=True,
                require_store=True,
            )
            self.memory_service = MemoryService(
                self.storage,
                self.memory_store,
            )
            self.checkpoint_pool = await open_checkpoint_pool(
                self.settings.database_url
            )
            self.checkpointer = AsyncPostgresSaver(self.checkpoint_pool)
            self.project_agents = {}
        except (
            DatabaseConfigurationError,
            DatabaseSchemaError,
            DatabaseUnavailableError,
        ) as exc:
            await self.close()
            self.startup_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 启动错误交给 Web UI 展示
            await self.close()
            self.startup_error = sanitize_text(str(exc))

    async def close(self) -> None:
        """按依赖顺序释放 Agent 使用的 Checkpointer 池和业务池。"""

        if self.memory_store_context is not None:
            try:
                await close_memory_store(self.memory_store_context)
            finally:
                self.memory_store_context = None
                self.memory_store = None
                self.memory_service = None
        if self.checkpoint_pool is not None:
            try:
                await self.checkpoint_pool.close()
            finally:
                self.checkpoint_pool = None
                self.checkpointer = None
        self.storage = None
        if self.database is not None:
            await self.database.close()
            self.database = None
        if self.project_agents is not None:
            self.project_agents.clear()
        self.project_agents = None

    @property
    def ready(self) -> bool:
        return (
            self.storage is not None
            and self.checkpointer is not None
            and self.memory_store is not None
            and self.memory_service is not None
            and self.startup_error is None
        )

    def status(self) -> dict[str, Any]:
        """返回不含凭据的服务状态。"""

        if self.startup_error:
            state = "error"
        elif not self.ready:
            state = "starting"
        else:
            state = "ready"
        settings = self.settings
        return {
            "status": state,
            "message": self.startup_error or "",
            "provider": settings.provider if settings else "",
            "model": settings.model_name if settings else "",
            "mcp_servers": sorted(settings.mcp_servers) if settings else [],
            "skills": ["/skills/"] if project_skills_enabled() else [],
            "database": "connected" if self.storage is not None else "",
            "memory_store": "connected" if self.memory_store is not None else "",
        }

    def require_ready(self) -> BusinessRepository:
        if not self.ready:
            raise RuntimeError(self.startup_error or "服务仍在启动，请稍候。")
        assert self.storage is not None
        return self.storage

    def project_workspace_dir(self, project: dict[str, Any]) -> Path:
        """把数据库中的受控相对路径解析为 Project 的真实工作目录。"""

        if self.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        root = self.settings.workspace_root.resolve()
        candidate = (root / str(project["workdir_path"])).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DatabaseSchemaError("Project 工作目录超出 workspace 根目录。") from exc
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    async def agent_for_project(self, project: dict[str, Any]) -> Any:
        """按 Project 缓存 Agent，使同一 Project 的会话共享文件后端。"""

        self.require_ready()
        if self.settings is None or self.checkpointer is None:
            raise RuntimeError("Agent 仍在启动，请稍候。")
        if self.memory_service is None:
            raise RuntimeError("Memory Store 仍在启动，请稍候。")
        if self.project_agents is None:
            self.project_agents = {}
        key = str(project["id"])
        cached = self.project_agents.get(key)
        if cached is not None:
            return cached
        agent = await build_research_agent(
            self.settings,
            checkpointer=self.checkpointer,
            workspace_dir=self.project_workspace_dir(project),
            memory_service=self.memory_service,
        )
        self.project_agents[key] = agent
        return agent

    @staticmethod
    def conversation_config(conversation_id: UUID) -> dict[str, Any]:
        return {"configurable": {"thread_id": str(conversation_id)}}
