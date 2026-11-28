"""每轮刷新已授权快照的 Skill 摘要，避免 Checkpoint 保留旧目录。"""

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


class SkillRefreshMiddleware(AgentMiddleware):
    state_schema = SkillsState

    def __init__(self, backend, sources, *, cache_snapshot: bool = False):
        self.loader = SkillsMiddleware(backend=backend, sources=sources, system_prompt=None)
        self._cache_snapshot = cache_snapshot
        self._cached_update: dict | None = None

    @staticmethod
    def _copy_update(update: dict) -> dict:
        copied = {"skills_load_errors": [], **update}
        if isinstance(copied.get("skills_metadata"), list):
            copied["skills_metadata"] = list(copied["skills_metadata"])
        return copied

    def before_agent(self, state, runtime: Runtime, config: RunnableConfig):
        # 不传旧的 skills_metadata；框架自己的 SkillsMiddleware 负责 prompt 渲染。
        if self._cache_snapshot and self._cached_update is not None:
            return self._copy_update(self._cached_update)
        update = self.loader.before_agent({}, runtime, config)
        if self._cache_snapshot:
            self._cached_update = update
        return self._copy_update(update)

    async def abefore_agent(self, state, runtime: Runtime, config: RunnableConfig):
        if self._cache_snapshot and self._cached_update is not None:
            return self._copy_update(self._cached_update)
        update = await self.loader.abefore_agent({}, runtime, config)
        if self._cache_snapshot:
            self._cached_update = update
        return self._copy_update(update)
