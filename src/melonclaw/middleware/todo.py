"""关闭任务清单时显式替换上游 profile 的 Todo 插槽。"""

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware


class DisabledTodoMiddleware(AgentMiddleware):
    # 保留官方状态定义，允许关闭后清理 Checkpoint 中的旧清单。
    state_schema = TodoListMiddleware.state_schema

    @property
    def name(self) -> str:
        return "TodoListMiddleware"

    def before_agent(self, state, runtime):
        return {"todos": []}

    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)
