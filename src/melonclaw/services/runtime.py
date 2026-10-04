"""应用运行时资源和 Agent 实例管理。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from melonclaw.core.agent import build_research_agent
from melonclaw.core.config import Settings, load_settings
from melonclaw.core.model_catalog import (
    ResolvedModel,
    catalog_item,
    is_custom_model_id,
    resolve_model_row,
)
from melonclaw.core.user_input import normalize_capabilities
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
from melonclaw.services.mcp import mcp_snapshot_revision, resolve_user_mcp_servers
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.skill_snapshot import clear_stale_snapshots, skill_snapshot
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import (
    GLOBAL_SKILLS_ROUTE,
    USER_SKILLS_ROUTE,
    SkillCatalog,
    SkillDefinition,
    SkillRoot,
)

logger = logging.getLogger(__name__)


@dataclass
class _AgentBuildLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


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
    attachment_hydration_provider: Any | None = None
    skill_install_provider: Any | None = None
    mcp_install_provider: Any | None = None
    workspace_agents: dict[Any, Any] | None = None
    agent_build_locks: dict[Any, _AgentBuildLock] = field(default_factory=dict)
    mcp_discovery: McpDiscoveryCoordinator = field(default_factory=McpDiscoveryCoordinator)
    startup_error: str | None = None
    worker_id: str = field(default_factory=lambda: f"web-{uuid4()}")

    async def initialize(self) -> None:
        """打开连接池并校验数据库、Memory 和模型配置。"""

        try:
            self.settings = load_settings()
            removed = clear_stale_snapshots(self.settings.data_root)
            if removed:
                logger.info("cleared %d stale skill snapshot(s) from previous process", removed)
            self.database = Database(self.settings.database_url)
            await self.database.open()
            self.storage = BusinessRepository(self.database)
            self.memory_store_context, self.memory_store = await open_memory_store(
                self.settings.database_url
            )
            # 建表和演示数据初始化由 melonclaw-db-init 独立执行，
            # 服务启动只检查当前结构，不修补旧表。
            await self.database.verify_schema(
                require_checkpointer=True,
                require_store=True,
            )
            converged = await self.storage.converge_stale_pending_messages()
            if converged:
                logger.info(
                    "converged %d stale pending assistant message(s) from previous process",
                    converged,
                )
            self.memory_service = MemoryService(
                self.storage,
                self.memory_store,
            )
            self.checkpoint_pool = await open_checkpoint_pool(
                self.settings.database_url
            )
            self.checkpointer = AsyncPostgresSaver(self.checkpoint_pool)
            self.workspace_agents = {}
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

        await self.mcp_discovery.close()

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
        if self.workspace_agents is not None:
            self.workspace_agents.clear()
        self.workspace_agents = None
        self.agent_build_locks.clear()

    @property
    def ready(self) -> bool:
        return (
            self.storage is not None
            and self.checkpointer is not None
            and self.memory_store is not None
            and self.memory_service is not None
            and self.startup_error is None
        )

    async def status(self) -> dict[str, Any]:
        """返回不含凭据的服务状态。"""

        if self.startup_error:
            state = "error"
        elif not self.ready:
            state = "starting"
        else:
            state = "ready"
        skill_routes = self._mounted_skill_routes("")
        mcp_slugs: list[str] = []
        if self.storage is not None and state == "ready":
            rows = await self.storage.list_visible_mcp_rows("")
            mcp_slugs = [str(row["slug"]) for row in rows if row["enabled"]]
        return {
            "status": state,
            "message": self.startup_error or "",
            "provider": "",
            "model": "",
            "mcp_servers": sorted(mcp_slugs),
            "skills": skill_routes,
            "database": "connected" if self.storage is not None else "",
            "memory_store": "connected" if self.memory_store is not None else "",
        }

    def _mounted_skill_routes(self, user_id: str) -> list[str]:
        """返回会为该用户挂载的 Skill 虚拟路由（目录存在才挂载）。"""

        if self.settings is None:
            return []
        return [
            route
            for route, directory in self._skill_dirs(user_id)
            if directory.is_dir()
        ]

    def _skill_dirs(self, user_id: str) -> tuple[tuple[str, Path], ...]:
        """共享 Skill 根与用户私有 Skill 根（虚拟路由前缀 → 目录）。"""

        assert self.settings is not None
        skills_root = self.settings.data_root / "skills"
        return (
            (GLOBAL_SKILLS_ROUTE, skills_root / "shared"),
            (USER_SKILLS_ROUTE, skills_root / "users" / user_id),
        )

    def _catalog_for(self, user_id: str) -> SkillCatalog:
        return SkillCatalog(
            SkillRoot(
                scope="global",
                route_prefix=GLOBAL_SKILLS_ROUTE,
                directory=directory,
            )
            if route == GLOBAL_SKILLS_ROUTE
            else SkillRoot(
                scope="user",
                route_prefix=USER_SKILLS_ROUTE,
                directory=directory,
            )
            for route, directory in self._skill_dirs(user_id)
        )

    async def visible_skills(self, user_id: str) -> list[SkillDefinition]:
        """某用户可见且启用的 Skill：磁盘扫描 ∩ 数据库可见行。

        同名共存时私有遮蔽共享：管理员发布的共享 Skill 不受个人私有
        占用影响，但拥有同名私有 Skill 的用户继续使用自己的版本，
        其 Agent 目录里只出现私有那一个，保证 skill id 无二义。
        """

        rows = await self.require_ready().list_visible_skill_rows(user_id, include_disabled=True)
        states = evaluate_skills(rows, self._catalog_for(user_id), self.settings.data_root / "skills")
        return [state.definition for state in states if state.effective_enabled and state.definition is not None]

    async def resolve_skill(
        self, user_id: str, skill_id: str | None
    ) -> SkillDefinition | None:
        """按用户可见性解析单个 Skill，供消息执行白名单校验。"""

        if not skill_id or not isinstance(skill_id, str):
            return None
        return next(
            (
                item
                for item in await self.visible_skills(user_id)
                if item.key == skill_id
            ),
            None,
        )

    async def _model_api_key(
        self, row: dict[str, Any], provider: dict[str, Any], user_id: str
    ) -> str | None:
        """个人模型只能使用本人的 Key；全局模型保留个人覆盖规则。"""
        storage = self.require_ready()
        if row["scope"] == "user":
            personal = await storage.get_user_provider_key(
                str(provider["provider_key"]), user_id
            )
            return str(personal["api_key"]) if personal else None
        return await storage.effective_provider_api_key(provider, user_id)

    async def models(self, user_id: str) -> dict[str, Any]:
        """返回指定用户可见的完整模型目录，不包含任何凭据。

        模型目录以数据库为唯一事实来源：全局共享模型（admin 配置，默认给
        全员）+ 该用户自建的私有模型，因此每个人看到的可用集合不一样。
        个人模型仅使用个人 Key，不受供应商全局开关影响；
        全局模型受供应商开关控制，保留个人 Key 优先、共享 Key 其次的规则。
        没有可用模型时返回空默认选择，等待用户配置。
        """

        if self.settings is None:
            return {"items": [], "default_model_id": ""}
        storage = self.require_ready()
        rows = await storage.list_visible_model_rows(user_id)
        items = []
        for row in rows:
            provider_row = await storage.get_provider_row(row["provider_key"])
            if provider_row is not None:
                effective = await self._model_api_key(row, provider_row, user_id)
                items.append(
                    catalog_item(row, {**provider_row, "api_key": effective})
                )
        available = sorted(
            [item for item in items if item["available"]],
            key=lambda item: (not item["is_default"], item["scope"] != "user" if item["is_default"] else item["scope"] != "global"),
        )
        default_model_id = next(
            (
                item["id"]
                for item in available
                if item.get("is_default")
            ),
            available[0]["id"] if available else "",
        )
        return {"items": items, "default_model_id": default_model_id}

    async def skills(self, user_id: str) -> dict[str, Any]:
        """返回指定用户可供前端选择的 Skill 元数据。"""

        items = await self.visible_skills(user_id)
        return {"items": [item.public_dict() for item in items]}

    async def resolve_model(
        self,
        user_id: str,
        model_id: str | None = None,
    ) -> ResolvedModel:
        """按用户解析模型：统一从 model_configs 表查询。

        - ``model_id=None``：取默认行（is_default 的可见启用行），无则
          第一条可见有 Key 的行，再无则提示先配置模型；
        - ``custom:`` 前缀：行不存在、不可见或已停用时报错；
        - 其他 ID：报错，模型目录只认数据库行。
        """

        if self.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        if model_id and is_custom_model_id(model_id):
            model_key = model_id.removeprefix("custom:")
            storage = self.require_ready()
            pair = await storage.get_model_with_provider(model_key)
            if pair is None:
                raise ValueError("所选模型不存在或当前不可用。")
            row, provider_row = pair
            if row["scope"] != "global" and row["created_by"] != user_id:
                raise ValueError("所选模型不存在或当前不可用。")
            effective = await self._model_api_key(row, provider_row, user_id)
            return resolve_model_row(
                row, {**provider_row, "api_key": effective}
            )
        elif model_id:
            raise ValueError("所选模型不存在或当前不可用。")
        return await self._resolve_default_model(user_id)

    async def _resolve_default_model(self, user_id: str) -> ResolvedModel:
        """解析当前默认模型：默认行 → 第一条可见有有效 Key 的行；没有则提示配置。"""

        assert self.settings is not None
        storage = self.require_ready()
        rows = await storage.list_visible_model_rows(user_id)
        candidates = []
        for row in rows:
            provider_row = await storage.get_provider_row(row["provider_key"])
            if provider_row is None or (
                row["scope"] == "global" and not provider_row["enabled"]
            ):
                continue
            effective = await self._model_api_key(row, provider_row, user_id)
            if effective:
                candidates.append(
                    (row, {**provider_row, "api_key": effective})
                )
        candidates.sort(key=lambda pair: (
            not pair[0]["is_default"],
            pair[0]["scope"] != "user" if pair[0]["is_default"] else pair[0]["scope"] != "global",
        ))
        default_pair = next(
            (pair for pair in candidates if pair[0]["is_default"]), None
        )
        selected = default_pair or (candidates[0] if candidates else None)
        if selected is not None:
            return resolve_model_row(*selected)
        raise ValueError("暂无可用模型，请先在技能|连接器 → 模型中配置供应商 Key 并添加模型。")

    async def model_for_message(
        self, user_id: str, message: dict[str, Any]
    ) -> ResolvedModel:
        """从消息的模型快照恢复模型绑定；快照为空时回落到当前默认模型。

        自定义模型被删除或停用后，历史消息回放回落到当前默认模型，
        避免旧会话因配置消失而无法打开。
        """

        snapshot = message.get("model") or {}
        model_id = snapshot.get("id")
        if model_id and is_custom_model_id(str(model_id)):
            try:
                return await self.resolve_model(user_id, str(model_id))
            except ValueError:
                return await self._resolve_default_model(user_id)
        return await self.resolve_model(
            user_id, str(model_id) if model_id else None
        )

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

    def conversation_workspace_dir(self, conversation_id: UUID) -> Path:
        """返回普通会话独享的持久工作目录。"""

        if self.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        root = self.settings.workspace_root.resolve()
        candidate = (root / "conversations" / str(conversation_id)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DatabaseSchemaError("Conversation 工作目录超出 workspace 根目录。") from exc
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def workspace_dir(
        self,
        conversation: dict[str, Any],
        project: dict[str, Any] | None,
    ) -> Path:
        if project is not None:
            return self.project_workspace_dir(project)
        return self.conversation_workspace_dir(UUID(str(conversation["id"])))

    async def agent_for_conversation(
        self,
        conversation: dict[str, Any],
        project: dict[str, Any] | None,
        model: ResolvedModel | None = None,
        capabilities: object = None,
    ) -> Any:
        """按工作区 + 模型版本 + 客户端能力缓存 Agent。

        能力必须进缓存键：同一个 Project 上，声明了提问能力的客户端和没声明的
        客户端拿到的是两个不同的 Agent（工具集不同），不能互相复用。
        """

        self.require_ready()
        if self.settings is None or self.checkpointer is None:
            raise RuntimeError("Agent 仍在启动，请稍候。")
        if self.memory_service is None:
            raise RuntimeError("Memory Store 仍在启动，请稍候。")
        if self.workspace_agents is None:
            self.workspace_agents = {}
        normalized_capabilities = normalize_capabilities(capabilities)
        user_id = str(conversation["user_id"])
        resolved_model = model or await self.resolve_model(user_id)
        workspace_key = (
            f"project:{user_id}:{project['id']}"
            if project is not None
            else f"conversation:{user_id}:{conversation['id']}"
        )
        storage = self.require_ready()
        # 三份快照互不依赖，独立连接读取；等待最慢的一份即可继续缓存命中判断。
        models_revision, skill_state, mcp_rows = await asyncio.gather(
            storage.models_revision(),
            skill_snapshot(storage, user_id, self.settings.data_root, self._catalog_for(user_id)),
            storage.list_visible_mcp_rows(user_id),
        )
        skills_revision, skill_dirs, skill_references = skill_state
        mcp_revision = mcp_snapshot_revision(mcp_rows)
        key = (
            workspace_key,
            resolved_model.cache_key,
            normalized_capabilities,
            skills_revision,
            mcp_revision,
            models_revision,
        )
        cached = self.workspace_agents.get(key)
        if cached is not None:
            self.workspace_agents.pop(key)
            self.workspace_agents[key] = cached
            return cached
        build_lock = self.agent_build_locks.setdefault(key, _AgentBuildLock())
        build_lock.users += 1
        try:
            async with build_lock.lock:
                cached = self.workspace_agents.get(key)
                if cached is not None:
                    return cached
                mcp_servers, mcp_allowlists = resolve_user_mcp_servers(mcp_rows)
                agent = await build_research_agent(
                    self.settings,
                    checkpointer=self.checkpointer,
                    workspace_dir=self.workspace_dir(conversation, project),
                    model=resolved_model,
                    memory_service=self.memory_service,
                    attachment_hydration_provider=self.attachment_hydration_provider,
                    skill_install_provider=self.skill_install_provider,
                    mcp_install_provider=self.mcp_install_provider,
                    client_capabilities=normalized_capabilities,
                    mcp_servers=mcp_servers,
                    mcp_tool_allowlists=mcp_allowlists,
                    skill_dirs=skill_dirs,
                )
                agent.melonclaw_skill_references = skill_references
                if not agent.melonclaw_mcp_failed:
                    self.workspace_agents[key] = agent
                cache_limit = max(1, self.settings.agent_cache_entries)
                while len(self.workspace_agents) > cache_limit:
                    self.workspace_agents.pop(next(iter(self.workspace_agents)))
                return agent
        finally:
            build_lock.users -= 1
            if build_lock.users == 0 and self.agent_build_locks.get(key) is build_lock:
                self.agent_build_locks.pop(key)

    @staticmethod
    def capabilities_for_message(message: dict[str, Any] | None) -> tuple[str, ...]:
        """读回某一轮消息落库时记录的客户端能力。

        恢复执行必须沿用原消息声明的能力，否则恢复用的 Agent 工具集和提问时
        不一致，可能出现“提问时有 ask_user、恢复后没有”的错位。
        """

        if not isinstance(message, dict):
            return ()
        metadata = message.get("display_metadata")
        if not isinstance(metadata, dict):
            return ()
        return normalize_capabilities(metadata.get("capabilities"))

    @staticmethod
    def conversation_config(conversation_id: UUID) -> dict[str, Any]:
        return {"configurable": {"thread_id": str(conversation_id)}}
