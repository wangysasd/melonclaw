"""任务执行的仓库审查清单、共享预算与稳定错误；不包含用户凭据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock

TASK_SEARCH_TOOL = "internet_search"
TASK_REVIEWED_TOOLS = frozenset({TASK_SEARCH_TOOL, "write_todos", "task", "read_task_history", "complete_task"})


class TaskExecutionError(RuntimeError):
    """可直接用于 TaskRun 错误码；消息不携带模型/远端原始响应。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class TaskRunControl:
    """单次运行主/子 Agent 共享；执行连接失效或取消时阻止后续模型和工具调用。"""

    model_limit: int
    tool_limit: int
    _stopped: Event = field(default_factory=Event, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _model_calls: int = field(default=0, init=False)
    _tool_calls: int = field(default=0, init=False)
    goal_complete: bool = field(default=False, init=False)

    def __post_init__(self):
        if self.model_limit < 1 or self.tool_limit < 1:
            raise ValueError("任务模型与工具调用预算必须为正数。")

    def stop(self) -> None:
        self._stopped.set()

    def check(self) -> None:
        if self._stopped.is_set():
            raise TaskExecutionError("execution_stopped", "任务执行已停止。")

    def complete(self):
        self.check()
        self.goal_complete = True

    def consume(self, kind: str) -> None:
        with self._lock:
            self.check()
            if kind == "model":
                if self._model_calls >= self.model_limit:
                    raise TaskExecutionError("model_limit", "任务模型调用次数已达上限。")
                self._model_calls += 1
            elif kind == "tool":
                if self._tool_calls >= self.tool_limit:
                    raise TaskExecutionError("tool_limit", "任务工具调用次数已达上限。")
                self._tool_calls += 1
            else:
                raise ValueError("未知调用预算类型。")


def task_allowed_tools(*, search_authorized: bool, todo_enabled: bool, history_available: bool = False) -> frozenset[str]:
    """只接受服务端解析的授权；不把浏览器/模型传来的工具名当成许可。"""

    names = {"task"}
    if search_authorized:
        names.add(TASK_SEARCH_TOOL)
    if todo_enabled:
        names.add("write_todos")
    if history_available:
        names.update({"read_task_history", "complete_task"})
    return frozenset(names) & TASK_REVIEWED_TOOLS
