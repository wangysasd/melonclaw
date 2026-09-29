"""每轮刷新已授权快照的 Skill 摘要，避免 Checkpoint 保留旧目录。"""

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


class SkillRefreshMiddleware(AgentMiddleware):
    state_schema = SkillsState

    def __init__(self, backend, sources):
        self.loader = SkillsMiddleware(backend=backend, sources=sources, system_prompt=None)

    def before_agent(self, state, runtime: Runtime, config: RunnableConfig):
        # 不传旧的 skills_metadata；框架自己的 SkillsMiddleware 负责 prompt 渲染。
        update = self.loader.before_agent({}, runtime, config)
        return {"skills_load_errors": [], **update}

    async def abefore_agent(self, state, runtime: Runtime, config: RunnableConfig):
        update = await self.loader.abefore_agent({}, runtime, config)
        return {"skills_load_errors": [], **update}
