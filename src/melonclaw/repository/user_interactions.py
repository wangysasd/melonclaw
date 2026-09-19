"""结构化用户问题的业务交互账本。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from melonclaw.database.schema import user_interactions
from melonclaw.repository.errors import (
    UserInteractionConflictError,
    UserInteractionExpiredError,
    UserInteractionNotFoundError,
)
from melonclaw.repository.mappers import _as_iso, _now
from melonclaw.repository.user_interaction_lifecycle import (
    ANSWERED_STATUSES,
    OPEN_STATUSES,
    UserInteractionStatus,
    transition_sources,
)


def _archived_interrupt_id(interrupt_id: str) -> str:
    """为已关闭的账本生成历史 interrupt 值，让出唯一键供新一轮提问使用。"""

    return f"{interrupt_id}#closed-{uuid4().hex[:8]}"


def _is_open_ledger(row: Any) -> bool:
    """只有仍在等待回答的账本可以复用；已关闭的账本必须让位给新一轮提问。"""

    return row is not None and str(row["status"]) == UserInteractionStatus.WAITING


def _answer_digest(answer: dict[str, Any]) -> str:
    encoded = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _interaction_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "assistant_message_id": str(row["assistant_message_id"]),
        "user_id": str(row["user_id"]),
        "interrupt_id": str(row["interrupt_id"]),
        "kind": str(row["kind"]),
        "payload": row["payload"] or {},
        "status": str(row["status"]),
        "decision_request_id": row["decision_request_id"],
        "answer": row["answer"],
        "answer_digest": row["answer_digest"],
        "reason_code": row["reason_code"],
        "created_at": _as_iso(row["created_at"]),
        "expires_at": _as_iso(row["expires_at"]),
        "accepted_at": _as_iso(row["accepted_at"]) if row["accepted_at"] else None,
        "resolved_at": _as_iso(row["resolved_at"]) if row["resolved_at"] else None,
    }


def _as_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class UserInteractionRepositoryMixin:
    async def create_or_get_user_interaction(
        self,
        conversation_id: UUID,
        assistant_message_id: UUID,
        user_id: str,
        interrupt_id: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> dict[str, Any]:
        """按 conversation + interrupt 幂等创建问题账本。

        同一个 interrupt 可能先后承载两道题：助手答完继续追问，或没收到答案又重问。
        只有上一本账本仍处于 waiting 时才复用；已经关了门的旧账本会被归档，
        然后开一本新的，避免浏览器拿到一个关了门的 interaction_id
        （提交后既不报错也不唤醒 Agent）。
        """

        if ttl_seconds < 1:
            raise ValueError("用户问题有效期必须是正整数。")
        async with self.engine.begin() as connection:
            existing = (
                await connection.execute(
                    select(user_interactions).where(
                        and_(
                            user_interactions.c.conversation_id == conversation_id,
                            user_interactions.c.interrupt_id == interrupt_id,
                        )
                    )
                )
            ).mappings().first()
            row = (
                existing
                if existing is not None
                and str(existing["status"]) == UserInteractionStatus.WAITING
                else None
            )
            if row is None:
                if existing is not None:
                    await connection.execute(
                        update(user_interactions)
                        .where(user_interactions.c.id == existing["id"])
                        .values(interrupt_id=_archived_interrupt_id(interrupt_id))
                    )
                created_at = _now()
                expires_at = created_at + timedelta(seconds=ttl_seconds)
                statement = pg_insert(user_interactions).values(
                    id=uuid4(),
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message_id,
                    user_id=user_id,
                    interrupt_id=interrupt_id,
                    kind="user_question",
                    payload=payload,
                    status=UserInteractionStatus.WAITING,
                    created_at=created_at,
                    expires_at=expires_at,
                ).on_conflict_do_nothing(
                    index_elements=[
                        user_interactions.c.conversation_id,
                        user_interactions.c.interrupt_id,
                    ]
                )
                row = (
                    await connection.execute(
                        statement.returning(user_interactions)
                    )
                ).mappings().first()
                if row is None:
                    # 并发下另一个请求先建了账本，直接复用它。
                    row = (
                        await connection.execute(
                            select(user_interactions).where(
                                and_(
                                    user_interactions.c.conversation_id == conversation_id,
                                    user_interactions.c.interrupt_id == interrupt_id,
                                )
                            )
                        )
                    ).mappings().first()
        if row is None:
            raise UserInteractionConflictError("用户问题状态无法保存，请稍后重试。")
        if str(row["user_id"]) != user_id or str(row["assistant_message_id"]) != str(assistant_message_id):
            raise UserInteractionConflictError("用户问题已绑定到另一条执行记录。")
        if str(row["status"]) != UserInteractionStatus.WAITING:
            raise UserInteractionConflictError("用户问题账本已关闭，请刷新会话。")
        if row["payload"] != payload:
            raise UserInteractionConflictError("用户问题内容已变化，请刷新会话。")
        return _interaction_dict(row)

    async def get_user_interaction(
        self,
        conversation_id: UUID,
        user_id: str,
        interaction_id: UUID,
    ) -> dict[str, Any] | None:
        query = select(user_interactions).where(
            and_(
                user_interactions.c.id == interaction_id,
                user_interactions.c.conversation_id == conversation_id,
                user_interactions.c.user_id == user_id,
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _interaction_dict(row) if row else None

    async def get_open_user_interaction(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        """返回会话中仍未关闭的问题账本，没有时避免构造 Agent 查询 Checkpoint。"""

        query = (
            select(user_interactions)
            .where(
                and_(
                    user_interactions.c.conversation_id == conversation_id,
                    user_interactions.c.user_id == user_id,
                    user_interactions.c.status.in_(OPEN_STATUSES),
                )
            )
            .order_by(user_interactions.c.created_at.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _interaction_dict(row) if row else None

    async def get_user_interaction_cancellation_candidate(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        """返回发新消息前确实需要收尾的交互。

        正常的 ``waiting`` 卡片不能被消息重试误取消；只有已经过期的 waiting、
        显式标成 expired 的账本，或答案已收但执行失败的 recovery_required 才是
        取消候选。recovery_required 优先，避免遗留的过期历史账本遮住当前失败轮次。
        """

        now = _now()
        query = (
            select(user_interactions)
            .where(
                and_(
                    user_interactions.c.conversation_id == conversation_id,
                    user_interactions.c.user_id == user_id,
                    or_(
                        user_interactions.c.status
                        == UserInteractionStatus.RECOVERY_REQUIRED,
                        user_interactions.c.status == UserInteractionStatus.EXPIRED,
                        and_(
                            user_interactions.c.status
                            == UserInteractionStatus.WAITING,
                            user_interactions.c.expires_at <= now,
                        ),
                    ),
                )
            )
            .order_by(
                case(
                    (
                        user_interactions.c.status
                        == UserInteractionStatus.RECOVERY_REQUIRED,
                        0,
                    ),
                    else_=1,
                ),
                user_interactions.c.created_at.desc(),
            )
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _interaction_dict(row) if row else None

    async def accept_user_interaction(
        self,
        conversation_id: UUID,
        user_id: str,
        interaction_id: UUID,
        decision_request_id: str,
        answer: dict[str, Any],
        *,
        allow_expired: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """条件接收答案；返回记录和是否首次接收。

        ``allow_expired`` 只用于服务端代答的取消：过期问题必须由 Agent 真正
        恢复一次才能解锁会话，因此这条路径允许接收已过期的账本。

        最终的 CAS 条件由转换表反向生成：只有 waiting / expired 允许进入
        accepted，业务上能走到这里的锁内快照也只可能是这两个状态。
        """

        now = _now()
        digest = _answer_digest(answer)
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(user_interactions)
                    .where(
                        and_(
                            user_interactions.c.id == interaction_id,
                            user_interactions.c.conversation_id == conversation_id,
                            user_interactions.c.user_id == user_id,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise UserInteractionNotFoundError
            record = _interaction_dict(row)
            if record["status"] in ANSWERED_STATUSES:
                same_request = record["decision_request_id"] == decision_request_id
                same_answer = record["answer_digest"] == digest
                if same_request and same_answer:
                    return record, False
                raise UserInteractionConflictError("这个用户问题已经提交过不同的答案。")

            expired = _as_datetime(row["expires_at"]) <= now
            if record["status"] == UserInteractionStatus.EXPIRED or (
                record["status"] == UserInteractionStatus.WAITING and expired
            ):
                if not allow_expired:
                    if record["status"] == UserInteractionStatus.WAITING:
                        await connection.execute(
                            update(user_interactions)
                            .where(user_interactions.c.id == interaction_id)
                            .values(
                                status=UserInteractionStatus.EXPIRED,
                                reason_code="ttl_expired",
                            )
                        )
                    raise UserInteractionExpiredError
                reason_code = "cancelled_after_expiry"
            elif record["status"] == UserInteractionStatus.WAITING:
                reason_code = None
            else:
                raise UserInteractionConflictError("这个用户问题已不再等待回答。")

            updated = await connection.execute(
                update(user_interactions)
                .where(
                    and_(
                        user_interactions.c.id == interaction_id,
                        user_interactions.c.status.in_(
                            transition_sources(UserInteractionStatus.ACCEPTED)
                        ),
                    )
                )
                .values(
                    status=UserInteractionStatus.ACCEPTED,
                    decision_request_id=decision_request_id,
                    answer=answer,
                    answer_digest=digest,
                    accepted_at=now,
                    reason_code=reason_code,
                )
                .returning(user_interactions)
            )
            accepted = updated.mappings().first()
            if accepted is None:
                raise UserInteractionConflictError
            return _interaction_dict(accepted), True

    async def resolve_user_interaction(
        self,
        conversation_id: UUID,
        interaction_id: UUID,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                update(user_interactions)
                .where(
                    and_(
                        user_interactions.c.id == interaction_id,
                        user_interactions.c.conversation_id == conversation_id,
                        user_interactions.c.status.in_(
                            transition_sources(UserInteractionStatus.RESOLVED)
                        ),
                    )
                )
                .values(
                    status=UserInteractionStatus.RESOLVED,
                    resolved_at=_now(),
                )
            )

    async def mark_user_interaction_recovery_required(
        self,
        conversation_id: UUID,
        interaction_id: UUID,
    ) -> bool:
        """答案已接收但这一轮没能跑完时标记，禁止任何人自动重放。

        只有当账本还停在 ``accepted``（还没被正常流程落到 ``resolved``）才改写：
        这说明答案已经交给 Agent，但恢复结果无法确认——既不知道 Agent 干到了
        哪一步，也不知道 Checkpoint 是否还挂着。此时宁可让用户重新来过，也不能
        猜一个结果出来，更不能把可能带副作用的那一段再跑一遍。

        返回是否真的标记成功，方便调用方在日志里区分「新标记」和「已经标记过」。
        """

        async with self.engine.begin() as connection:
            updated = await connection.execute(
                update(user_interactions)
                .where(
                    and_(
                        user_interactions.c.id == interaction_id,
                        user_interactions.c.conversation_id == conversation_id,
                        user_interactions.c.status.in_(
                            transition_sources(
                                UserInteractionStatus.RECOVERY_REQUIRED
                            )
                        ),
                    )
                )
                .values(
                    status=UserInteractionStatus.RECOVERY_REQUIRED,
                    reason_code="recovery_required",
                )
                .returning(user_interactions)
            )
            row = updated.mappings().first()
        return row is not None

    async def get_recovery_required_interaction(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        """返回会话里等待人工结束的问题账本。

        ``recovery_required`` 不在 ``get_open_user_interaction`` 返回的开放集合里：
        它不是"还在等答案"，而是"答案进去了、结果没人认领"。历史把它表成失败，
        解锁则由用户明确发起新一轮（发新消息）时走取消通道完成。
        """

        query = (
            select(user_interactions)
            .where(
                and_(
                    user_interactions.c.conversation_id == conversation_id,
                    user_interactions.c.user_id == user_id,
                    user_interactions.c.status
                    == UserInteractionStatus.RECOVERY_REQUIRED,
                )
            )
            .order_by(user_interactions.c.accepted_at.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _interaction_dict(row) if row else None

    async def discard_recovery_required_interaction(
        self,
        conversation_id: UUID,
        interaction_id: UUID,
    ) -> None:
        """开始取消恢复后关闭旧失败账本，避免它永久遮住后续状态。

        这里只用常量精确匹配 recovery_required，而不是转换表反向生成的
        ``transition_sources(DISCARDED)``：后者还包含 waiting，而运行时代码
        不允许把未过期的 waiting 卡片直接丢弃（那条转换只属于数据库迁移
        的历史清理）。
        """

        async with self.engine.begin() as connection:
            await connection.execute(
                update(user_interactions)
                .where(
                    and_(
                        user_interactions.c.id == interaction_id,
                        user_interactions.c.conversation_id == conversation_id,
                        user_interactions.c.status
                        == UserInteractionStatus.RECOVERY_REQUIRED,
                    )
                )
                .values(
                    status=UserInteractionStatus.DISCARDED,
                    reason_code="recovery_cancel_started",
                    resolved_at=_now(),
                )
            )
