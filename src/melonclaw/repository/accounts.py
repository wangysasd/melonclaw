"""账户管理、可撤销会话和身份修改的事务边界。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import (
    auth_sessions,
    chat_conversations,
    chat_messages,
    tenants,
    users,
)
from melonclaw.repository.mappers import _now


class AccountConflict(ValueError):
    """存在活动请求或任务，无法修改身份归属。"""


class AccountRepositoryMixin:
    @asynccontextmanager
    async def account_guard(self, *, exclusive=False):
        """业务请求持共享锁；管理变更用非阻塞独占锁防止执行准备竞态。"""
        async with self._account_slots, self.engine.begin() as conn:
            if exclusive:
                ok = (await conn.execute(text(
                    "SELECT pg_try_advisory_xact_lock(hashtext(current_schema()), 741923)"
                ))).scalar()
                if not ok:
                    raise AccountConflict("当前有请求正在执行，请稍后重试。")
            else:
                await conn.execute(text("SELECT pg_advisory_xact_lock_shared(hashtext(current_schema()), 741923)"))
            yield conn

    async def account_users(self):
        query = select(
            users.c.user_id, users.c.user_name_zh, users.c.tenant_id,
            users.c.tenant_role, users.c.tenant_status, users.c.created_at,
            tenants.c.tenant_name_zh, tenants.c.enabled.label("tenant_enabled"),
        ).join(tenants).where(users.c.tenant_status == "active").order_by(users.c.user_id)
        async with self.engine.connect() as conn:
            return [dict(row) for row in (await conn.execute(query)).mappings()]

    async def account_tenants(self):
        async with self.engine.connect() as conn:
            return [dict(row) for row in (await conn.execute(
                select(tenants).order_by(tenants.c.tenant_id)
            )).mappings()]

    async def account_password(self, user_id):
        async with self.engine.connect() as conn:
            return (await conn.execute(select(users.c.password_hash).join(tenants).where(
                users.c.user_id == user_id, users.c.tenant_status == "active",
                tenants.c.enabled.is_(True),
            ))).scalar_one_or_none()

    async def _account_idle(self, conn, user_ids):
        active = select(chat_messages.c.id).join(chat_conversations).where(
            chat_conversations.c.user_id.in_(user_ids),
            chat_messages.c.role == "assistant",
            chat_messages.c.status.in_(["pending", "interrupted"]),
        ).limit(1)
        if (await conn.execute(active)).first():
            raise AccountConflict("用户还有运行中或等待审批／回答的任务，请先结束任务。")

    async def save_account_user(self, user_id, values, *, create=False):
        values = {**values, "updated_at": _now()}
        try:
            async with self.account_guard(exclusive=True) as conn:
                target = (await conn.execute(select(tenants.c.tenant_id).where(
                    tenants.c.tenant_id == values["tenant_id"], tenants.c.enabled.is_(True),
                ))).first()
                if not target:
                    raise ValueError("请选择启用中的租户。")
                if create:
                    await conn.execute(insert(users).values(
                        user_id=user_id, created_at=_now(), tenant_role="member", **values,
                    ))
                else:
                    old = (await conn.execute(select(users).where(
                        users.c.user_id == user_id, users.c.tenant_status == "active",
                    ))).mappings().first()
                    if not old:
                        raise ValueError("用户不存在。")
                    if old["tenant_id"] != values["tenant_id"]:
                        await self._account_idle(conn, [user_id])
                    await conn.execute(update(users).where(users.c.user_id == user_id).values(**values))
        except IntegrityError as exc:
            raise ValueError("用户 ID 已存在，不能重复使用。") from exc

    async def delete_account_user(self, user_id):
        async with self.account_guard(exclusive=True) as conn:
            await self._account_idle(conn, [user_id])
            result = await conn.execute(update(users).where(
                users.c.user_id == user_id, users.c.tenant_status == "active",
            ).values(tenant_status="deleted", updated_at=_now()))
            if not result.rowcount:
                raise ValueError("用户不存在。")
            await self._revoke_user_sessions(conn, user_id)

    async def set_account_password(self, user_id, password_hash):
        async with self.account_guard(exclusive=True) as conn:
            result = await conn.execute(update(users).where(
                users.c.user_id == user_id, users.c.tenant_status == "active",
            ).values(password_hash=password_hash, updated_at=_now()))
            if not result.rowcount:
                raise ValueError("用户不存在。")
            await self._revoke_user_sessions(conn, user_id)

    async def _revoke_user_sessions(self, conn, user_id):
        await conn.execute(delete(auth_sessions).where(or_(
            auth_sessions.c.user_id == user_id, auth_sessions.c.login_user_id == user_id,
        )))

    async def save_account_tenant(self, tenant_id, values, *, create=False):
        values = {**values, "updated_at": _now()}
        try:
            async with self.account_guard(exclusive=True) as conn:
                if create:
                    await conn.execute(insert(tenants).values(
                        tenant_id=tenant_id, created_at=_now(), **values,
                    ))
                else:
                    if not values["enabled"]:
                        ids = (await conn.execute(select(users.c.user_id).where(
                            users.c.tenant_id == tenant_id,
                        ))).scalars().all()
                        await self._account_idle(conn, ids)
                        for user_id in ids:
                            await self._revoke_user_sessions(conn, user_id)
                    result = await conn.execute(update(tenants).where(
                        tenants.c.tenant_id == tenant_id,
                    ).values(**values))
                    if not result.rowcount:
                        raise ValueError("租户不存在。")
        except IntegrityError as exc:
            raise ValueError("租户 ID 或名称已存在。") from exc

    async def create_auth_session(self, token_hash, user_id):
        async with self.engine.begin() as conn:
            await conn.execute(delete(auth_sessions).where(auth_sessions.c.expires_at <= _now()))
            await conn.execute(insert(auth_sessions).values(
                token_hash=token_hash, user_id=user_id, login_user_id=user_id,
                created_at=_now(), expires_at=_now() + timedelta(days=7),
            ))

    async def get_auth_session(self, token_hash):
        async with self.engine.connect() as conn:
            return (await conn.execute(select(auth_sessions).where(
                auth_sessions.c.token_hash == token_hash, auth_sessions.c.expires_at > _now(),
            ))).mappings().first()

    async def switch_auth_session(self, token_hash, user_id):
        async with self.engine.begin() as conn:
            result = await conn.execute(update(auth_sessions).where(
                auth_sessions.c.token_hash == token_hash, auth_sessions.c.expires_at > _now(),
            ).values(user_id=user_id))
            if not result.rowcount:
                raise ValueError("登录已失效。")

    async def delete_auth_session(self, token_hash):
        async with self.engine.begin() as conn:
            await conn.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == token_hash))
