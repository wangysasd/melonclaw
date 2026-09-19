"""Agent 执行的收尾：失败/取消状态落库、用户问题账本封账、会话锁释放。

这几件事的共同点是"写最终状态、吞掉次要错误"，和流式编排本身无关；单独拎出来
也让 ``ExecutionService`` 不会被这些辅助分支撑到超过拆分上限。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from melonclaw.repository import BusinessRepository

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示，避免与 execution 模块循环导入
    from melonclaw.services.execution import PreparedExecution

logger = logging.getLogger(__name__)


async def mark_interaction_recovery_required(
    storage: BusinessRepository | None,
    conversation_id: UUID,
    interaction_id: UUID | None,
) -> None:
    """答案已收但这一轮没跑完时，把账本钉成"等待人工结束"。

    只在恢复执行（回答/审批/代答）的路径上有意义：新提的消息没有账本。
    标记失败只记日志：这一次失败最多让账本停在 accepted（等于改动前的行为），
    不能把 Agent 的原始错误顶掉。
    """

    if interaction_id is None or storage is None:
        return
    try:
        await storage.mark_user_interaction_recovery_required(
            conversation_id,
            interaction_id,
        )
    except Exception:  # noqa: BLE001 - 不覆盖原始 Agent/取消错误
        logger.warning(
            "标记用户问题为待恢复失败：interaction_id=%s",
            interaction_id,
            exc_info=True,
        )


async def mark_execution_status(
    storage: BusinessRepository | None,
    execution: "PreparedExecution",
    *,
    expected_status: str | tuple[str, ...],
    status: str,
    error_code: str,
    display_metadata: list[dict[str, Any]],
) -> None:
    """写入助手的最终状态；写不进去不抛错，保留原始异常给上层。"""

    if storage is None:
        return
    try:
        await storage.update_assistant(
            execution.conversation_id,
            execution.assistant_message_id,
            status=status,
            display_metadata={"events": display_metadata} if display_metadata else {},
            error_code=error_code,
            expected_status=expected_status,
        )
    except Exception:  # noqa: BLE001 - 不覆盖原始 Agent/取消错误
        # 原始 Agent/取消错误优先；下一次历史查询仍会显示已存在的业务状态。
        return


async def release_execution(
    storage: BusinessRepository | None,
    execution: "PreparedExecution",
) -> None:
    """释放本轮执行持有的会话锁；幂等，重复调用安全。"""

    if execution.released or execution.lock_connection is None:
        execution.released = True
        return
    execution.released = True
    if storage is not None:
        await storage.release_advisory_lock(
            execution.lock_connection,
            execution.conversation_id,
        )


__all__ = [
    "mark_execution_status",
    "mark_interaction_recovery_required",
    "release_execution",
]
