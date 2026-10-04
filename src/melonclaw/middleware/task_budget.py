"""官方 thread 调用计数按业务用户消息分段；审批恢复不清零。"""

from typing import Annotated

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from typing_extensions import NotRequired

from melonclaw.core.tool_catalog import selection_turn


class TaskBudgetState(AgentState):
    budget_task_id: NotRequired[Annotated[str, PrivateStateAttr]]


class TaskBudgetMiddleware(AgentMiddleware):
    state_schema = TaskBudgetState

    def __init__(self, *, reset_todos: bool = True):
        self.reset_todos = reset_todos

    async def abefore_agent(self, state, runtime):
        turn = selection_turn(state, runtime.context)
        if turn and state.get("budget_task_id") != turn:
            update = {"budget_task_id": turn, "thread_model_call_count": 0,
                      "thread_tool_call_count": {}}
            if self.reset_todos:
                update["todos"] = []
            return update
        return None
