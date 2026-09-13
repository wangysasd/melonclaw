"""数据库层共享数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserContext:
    """经过数据库校验的用户和租户上下文。"""

    user_id: str
    user_name_zh: str
    tenant_id: str
    tenant_name_zh: str
    tenant_role: str = "member"
    tenant_status: str = "active"

@dataclass(frozen=True)
class RequestRecord:
    """一次 request_id 对应的业务消息对。"""

    request_id: str
    content: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]


@dataclass(frozen=True)
class PreparedMessagePair:
    """短事务提交后的消息 ID，供流式执行和最终更新使用。"""

    request_id: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]


