"""任务执行的仓库审查清单、共享预算与稳定错误；不包含用户凭据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock

TASK_SEARCH_TOOL = "internet_search"
TASK_REVIEWED_TOOLS = frozenset({
    TASK_SEARCH_TOOL, "write_todos", "task", "read_task_history", "complete_task",
    "list_mcp_tools", "find_tools", "ls", "read_file",
})


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
    _discoveries: set[str] = field(default_factory=set, init=False, repr=False)
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

    def reserve_discovery(self, key: str, limit: int) -> bool:
        """主/子 Agent 共用本次运行的发现额度；已处理请求不重复扣减。"""
        with self._lock:
            self.check()
            if key in self._discoveries:
                return True
            if len(self._discoveries) >= limit:
                return False
            self._discoveries.add(key)
            return True

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


def task_allowed_tools(
    *, search_authorized: bool, todo_enabled: bool, history_available: bool = False,
    skills_available: bool = False, mcp_tool_names: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """只接受服务端解析的授权；不把浏览器/模型传来的工具名当成许可。"""

    names = {"task", "list_mcp_tools", "find_tools"}
    if search_authorized:
        names.add(TASK_SEARCH_TOOL)
    if todo_enabled:
        names.add("write_todos")
    if history_available:
        names.update({"read_task_history", "complete_task"})
    if skills_available:
        names.update({"ls", "read_file"})
    # 外部名称只能来自本次服务端发现并通过白名单的真实工具，不按名称前缀放行。
    return (frozenset(names) & TASK_REVIEWED_TOOLS) | mcp_tool_names
