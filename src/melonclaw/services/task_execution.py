"""不依赖聊天消息表的无人值守执行核心，供 Web 内置执行器调用。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage

from melonclaw.core.agent import AgentContext
from melonclaw.core.config import Settings
from melonclaw.core.task_policy import TaskExecutionError, TaskRunControl
from melonclaw.core.task_snapshot import task_thread_id
from melonclaw.output.content import answer_text
from melonclaw.output.formatting import sanitize_text


@dataclass(frozen=True)
class TaskOutcome:
    status: str
    result_markdown: str
    result_is_partial: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class TaskProgress:
    """白名单公开投影；不接受 reasoning、工具参数/原始结果或任意事件字段。"""

    result_limit: int
    text: str = ""
    complete_answer: bool = False

    def __post_init__(self):
        if self.result_limit < 1:
            raise ValueError("任务结果限制必须为正数。")

    def consume(self, namespace, update: dict) -> None:
        if update.get("__interrupt__"):
            raise TaskExecutionError("interaction_required", "任务需要人工交互，无法无人值守完成。")
        for state in update.values():
            if not isinstance(state, dict):
                continue
            messages = state.get("messages", [])
            if not isinstance(messages, (list, tuple)):
                messages = [messages]
            for message in messages:
                if isinstance(message, AIMessage):
                    if not namespace:
                        text = sanitize_text(answer_text(message.content))
                        self.complete_answer = not message.tool_calls and bool(text.strip())
                        if text:
                            self.text = text[: self.result_limit]
                        if len(text) > self.result_limit:
                            raise TaskExecutionError(
                                "result_limit", "文字结果超过上限，已保留不完整内容。"
                            )


async def execute_task_agent(
    agent: Any,
    *,
    run_id: UUID,
    instruction: str,
    context: AgentContext,
    control: TaskRunControl,
    settings: Settings,
    progress: TaskProgress | None = None,
) -> TaskOutcome:
    """终态只表示本地执行结果；执行器必须在持有单实例会话锁的连接上提交才算完成。

    此函数不写 TaskRun、聊天、通知或日志，不自动恢复任何 interrupt。
    执行器在取消/会话丢失时应同时 control.stop() 和 cancel 当前协程。
    """
    if context.memory_enabled or context.memory_admin or context.conversation_id:
        raise ValueError("任务上下文不能绑定聊天或启用长期记忆。")
    if not instruction.strip() or len(instruction.strip()) > 12_000:
        raise ValueError("任务要求或执行时限无效。")
    progress = (
        progress
        if progress is not None
        else TaskProgress(
            settings.task_result_max_chars,
        )
    )
    config = {"configurable": {"thread_id": task_thread_id(run_id)}}
    status, code, error = "succeeded", None, None
    try:
        control.check()
        async with asyncio.timeout(settings.task_execution_timeout_seconds):
            async for namespace, update in agent.astream(
                {"messages": [HumanMessage(content=instruction.strip())]},
                config,
                context=context,
                stream_mode="updates",
                subgraphs=True,
            ):
                control.check()
                progress.consume(namespace, update)
            # 检查真实 checkpoint 状态；未知类型的中断也不视为成功。
            state = await agent.aget_state(config)
            if state.interrupts or state.next:
                raise TaskExecutionError(
                    "interaction_required", "任务需要人工交互，无法无人值守完成。"
                )
            if not progress.complete_answer or not progress.text.strip():
                raise TaskExecutionError("empty_result", "任务未生成可用文字结果。")
    except asyncio.CancelledError:
        control.stop()
        status, code, error = (
            "cancelled",
            "cancelled",
            "本地执行已确认停止；已发出的远端请求可能仍在处理。",
        )
    except TimeoutError:
        control.stop()
        status, code, error = "failed", "execution_timeout", "任务执行超过时限。"
    except TaskExecutionError as exc:
        control.stop()
        status, code, error = "failed", exc.code, str(exc)
    except Exception:
        control.stop()
        # 不把模型、数据库、HTTP 异常原文转入结果或日志。
        status, code, error = (
            "failed",
            "execution_failed",
            "任务执行失败，请检查模型和当前授权配置。",
        )
    return TaskOutcome(status, progress.text, status != "succeeded", code, error)
