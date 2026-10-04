"""主 Agent 与子 Agent 共用的官方运行控制组装。"""

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.runnables import RunnableConfig
from langgraph.constants import TAG_NOSTREAM

from melonclaw.core.agent_errors import retry_transient_model_error
from melonclaw.core.config import Settings
from melonclaw.core.model_catalog import DEFAULT_CONTEXT_WINDOW
from melonclaw.middleware.summarization import ToolPoolSummarizationMiddleware
from melonclaw.middleware.task_budget import TaskBudgetMiddleware
from melonclaw.middleware.todo import DisabledTodoMiddleware
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.middleware.usage_observation import UsageObservationMiddleware

TODO_GUIDANCE = "复杂任务（三个及以上步骤）可使用 write_todos 规划并更新进度。简单问答直接回答，不建立清单。新任务不要沿用旧任务的清单。"


def build_agent_controls(
    model, backend, settings: Settings, *,
    context_window: int = DEFAULT_CONTEXT_WINDOW, scope: str = "main",
    tool_pool: ToolPoolMiddleware | None = None,
):
    trigger_tokens = settings.summary_trigger_tokens(context_window)
    # 摘要模型继承执行 callback，但摘要正文不能混入聊天流。
    summary_model = model.with_config(RunnableConfig(tags=[TAG_NOSTREAM]))
    controls = [
        ToolPoolSummarizationMiddleware(
            model=summary_model, backend=backend, tool_pool=tool_pool,
            trigger=("tokens", trigger_tokens),
            keep=("tokens", settings.agent_summary_keep_tokens),
            trim_tokens_to_summarize=trigger_tokens,
        ),
        TaskBudgetMiddleware(reset_todos=settings.agent_todo_enabled),
        ModelCallLimitMiddleware(thread_limit=settings.agent_model_call_limit, exit_behavior="error"),
        ToolCallLimitMiddleware(thread_limit=settings.agent_tool_call_limit, exit_behavior="error"),
        ModelRetryMiddleware(max_retries=settings.agent_retry_max_retries,
                             initial_delay=settings.agent_retry_initial_delay,
                             max_delay=settings.agent_retry_max_delay,
                             retry_on=retry_transient_model_error, on_failure="error"),
    ]
    if settings.agent_todo_enabled:
        controls.append(TodoListMiddleware(system_prompt=TODO_GUIDANCE))
    else:
        controls.append(DisabledTodoMiddleware())
    if settings.agent_usage_enabled:
        controls.append(UsageObservationMiddleware(context_window=context_window,
                                                   trigger_tokens=trigger_tokens,
                                                   scope=scope))
    return controls
