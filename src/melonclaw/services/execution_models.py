"""聊天执行准备结果；不携带任务报告正文。"""
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.model_catalog import ResolvedModel


@dataclass
class PreparedExecution:
    """准备阶段完成后的执行句柄。锁连接会一直持有到流结束。"""

    conversation_id: UUID
    project_id: UUID | None
    project_name: str | None
    workdir_path: str
    user_id: str
    tenant_id: str
    tenant_name: str
    tenant_role: str
    tenant_status: str
    request_id: str
    run_id: str
    worker_id: str
    config: dict[str, Any]
    assistant_message_id: UUID
    agent: Any | None
    model: ResolvedModel
    lock_connection: AsyncConnection | None = None
    user_message_id: UUID | None = None
    content: str = ""
    skill_id: str | None = None
    user_interaction_id: UUID | None = None
    resuming: bool = False
    replay_message: dict[str, Any] | None = None
    assistant_steps: list[dict[str, Any]] = field(default_factory=list)
    execution_duration_ms: int | None = None
    preparation_duration_ms: int = 0
    timings: dict[str, Any] | None = None
    display_events: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    task_context: dict[str, Any] = field(default_factory=dict)
    released: bool = False

