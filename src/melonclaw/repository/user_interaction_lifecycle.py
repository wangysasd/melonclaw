"""user_interactions 账本的状态生命周期。

六个状态与 ``database/schema.py`` 的 CHECK 约束一一对应，两边由
``tests/test_user_input.py`` 校验不漂移。规则只有两条：

1. 状态值只在这里定义；repository 与 services 一律 import 常量，不再手写
   字面量——「什么状态能变到什么状态」不允许在多个文件里各写一遍。
2. 写库时的 ``where(status=...)`` / CAS 条件由 :func:`transition_sources`
   从转换表反向生成，不各自手写集合。

加一个新状态时：改这里的常量与转换表、改 schema.py 的 CHECK 约束、加一次
数据库迁移，三处都在同一张表上对齐。
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping


class UserInteractionStatus(StrEnum):
    """账本状态；语义见 docs/design-docs/user-input-hitl.md。"""

    WAITING = "waiting"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    RECOVERY_REQUIRED = "recovery_required"
    DISCARDED = "discarded"


ALL_STATUSES: Final = frozenset(UserInteractionStatus)

# 允许的状态转换：source -> 允许到达的目标集合。
#
# - waiting -> discarded 只发生在数据库迁移的历史清理（把同一会话多余的
#   waiting 账本收档）；运行时代码不允许把未过期的 waiting 卡片直接丢弃。
# - expired -> accepted 只用于服务端代答取消：过期账本必须被 Agent 真正
#   恢复一次才能解锁会话，因此原地接收，而不是归档后另开新账本。
# - resolved / discarded 是终态：账本是审计记录，关闭后只归档、不回滚。
_TRANSITIONS: Final[Mapping[UserInteractionStatus, frozenset[UserInteractionStatus]]] = MappingProxyType(
    {
        UserInteractionStatus.WAITING: frozenset(
            {
                UserInteractionStatus.ACCEPTED,
                UserInteractionStatus.EXPIRED,
                UserInteractionStatus.DISCARDED,
            }
        ),
        UserInteractionStatus.ACCEPTED: frozenset(
            {
                UserInteractionStatus.RESOLVED,
                UserInteractionStatus.RECOVERY_REQUIRED,
            }
        ),
        UserInteractionStatus.EXPIRED: frozenset({UserInteractionStatus.ACCEPTED}),
        UserInteractionStatus.RECOVERY_REQUIRED: frozenset(
            {UserInteractionStatus.DISCARDED}
        ),
        UserInteractionStatus.RESOLVED: frozenset(),
        UserInteractionStatus.DISCARDED: frozenset(),
    }
)


def can_transition(
    source: UserInteractionStatus,
    target: UserInteractionStatus,
) -> bool:
    """source 是否允许直接变到 target。"""

    return target in _TRANSITIONS[source]


def transition_sources(
    target: UserInteractionStatus,
) -> frozenset[UserInteractionStatus]:
    """允许进入 target 的源状态集合。

    写库时的 ``where(status.in_(...))`` / CAS 条件由它生成：转换表只写
    一处，每个 UPDATE 不再各自定义「什么状态能变到什么状态」。
    """

    return frozenset(
        source for source, targets in _TRANSITIONS.items() if target in targets
    )


# ---- 语义分组（查询用，不是转换）----

# 会话仍被这轮交互占着：在等答案，或答案已收但执行尚未收尾。
OPEN_STATUSES: Final = frozenset(
    {UserInteractionStatus.WAITING, UserInteractionStatus.ACCEPTED}
)

# 已经收过答案：同一答案的重试返回幂等回执，不同答案被拒绝。
ANSWERED_STATUSES: Final = frozenset(
    {UserInteractionStatus.ACCEPTED, UserInteractionStatus.RESOLVED}
)

# 这轮还没收到答案：waiting=仍在等，expired=已过期但答案没进来。
UNANSWERED_STATUSES: Final = frozenset(
    {UserInteractionStatus.WAITING, UserInteractionStatus.EXPIRED}
)


__all__ = [
    "ALL_STATUSES",
    "ANSWERED_STATUSES",
    "OPEN_STATUSES",
    "UNANSWERED_STATUSES",
    "UserInteractionStatus",
    "can_transition",
    "transition_sources",
]
