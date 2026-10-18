"""环境变量 CRUD、版本 CAS 与聊天审批提交的事务边界。"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import environment_operations as operations
from melonclaw.database.schema import environment_variables as variables
from melonclaw.database.schema import model_providers
from melonclaw.repository.mappers import _now
from melonclaw.repository.resource_versions import resource_change


class EnvironmentConflict(ValueError):
    status_code = 409


def target_filter(change):
    return and_(
        variables.c.scope == change["scope"],
        variables.c.owner_user_id.is_(None)
        if change["scope"] == "global"
        else variables.c.owner_user_id == change["owner_user_id"],
        variables.c.name == change["name"],
    )


class EnvironmentRepositoryMixin:
    async def list_environment(self, user_id):
        async with self.engine.connect() as conn:
            return [
                dict(row)
                for row in (
                    await conn.execute(
                        select(variables)
                        .where(
                            or_(variables.c.scope == "global", variables.c.owner_user_id == user_id)
                        )
                        .order_by(variables.c.enabled.desc(), variables.c.name.asc())
                    )
                ).mappings()
            ]

    async def effective_environment(self, user_id):
        rows = await self.list_environment(user_id)
        result = {}
        for scope in ("global", "user"):
            result.update(
                {
                    row["name"]: row["value"]
                    for row in rows
                    if row["scope"] == scope and row["enabled"]
                }
            )
        async with self.engine.connect() as conn:
            reserved = (await conn.execute(select(model_providers.c.api_key_env))).scalars().all()
        return {key: value for key, value in result.items() if key not in reserved}

    @resource_change(payload="change")
    async def save_environment(self, change):
        try:
            async with self.engine.begin() as conn:
                return await self._mutate_environment(conn, change)
        except IntegrityError:
            raise EnvironmentConflict("同一范围内变量名已存在，请刷新后重试。") from None

    async def _mutate_environment(self, conn, change):
        action = change["action"]
        fields = dict(change.get("fields", {}))
        timestamp = _now()
        if action != "delete":
            candidates = [
                dict(row)
                for row in (
                    await conn.execute(
                        select(variables).where(
                            variables.c.scope == change["scope"],
                            variables.c.owner_user_id.is_(None)
                            if change["scope"] == "global"
                            else variables.c.owner_user_id == change["owner_user_id"],
                        )
                    )
                ).mappings()
            ]
            candidate = next(
                (row for row in candidates if str(row["id"]) == change.get("id")), None
            )
            if candidate is None:
                candidate = {"name": change["name"], "enabled": True, "value": ""}
                candidates.append(candidate)
            candidate.update(fields)
            size = sum(
                len(row["name"].encode()) + len(row["value"].encode()) + 2
                for row in candidates
                if row["enabled"]
            )
            if size > 65536:
                raise EnvironmentConflict("当前范围启用的环境变量总大小不能超过 64 KiB。")
        if action == "create":
            count = (
                await conn.execute(
                    select(func.count())
                    .select_from(variables)
                    .where(
                        variables.c.scope == change["scope"],
                        variables.c.owner_user_id.is_(None)
                        if change["scope"] == "global"
                        else variables.c.owner_user_id == change["owner_user_id"],
                    )
                )
            ).scalar_one()
            if count >= 200:
                raise EnvironmentConflict("当前范围最多配置 200 项环境变量。")
            identifier = uuid4()
            await conn.execute(
                insert(variables).values(
                    id=identifier,
                    name=change["name"],
                    scope=change["scope"],
                    owner_user_id=change["owner_user_id"],
                    created_by=change["created_by"],
                    version=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                    **fields,
                )
            )
            return {"id": str(identifier), "version": 1, "scope": change["scope"]}
        predicate = and_(
            target_filter(change),
            variables.c.id == UUID(change["id"]),
            variables.c.version == change["expected_version"],
        )
        statement = (
            delete(variables).where(predicate)
            if action == "delete"
            else update(variables)
            .where(predicate)
            .values(**fields, version=variables.c.version + 1, updated_at=timestamp)
        )
        result = await conn.execute(statement)
        if result.rowcount != 1:
            raise EnvironmentConflict("变量已变化或已删除，请刷新后重新审批。")
        return {
            "id": change["id"],
            "version": change["expected_version"] + 1,
            "scope": change["scope"],
            "deleted": action == "delete",
        }

    async def cleanup_environment_operations(self):
        async with self.engine.begin() as conn:
            await self._expire_environment_operations(conn)

    async def _expire_environment_operations(self, conn):
        await conn.execute(
            update(operations)
            .where(
                operations.c.expires_at <= _now(),
                operations.c.status.in_(["staged", "prepared"]),
            )
            .values(payload=None, status="expired")
        )
        await conn.execute(
            update(operations)
            .where(
                operations.c.created_at < _now() - timedelta(days=30),
                operations.c.response.is_not(None),
            )
            .values(response=None)
        )

    async def stage_environment(self, identifier, user_id, conversation_id, request_id, change):
        async with self.engine.begin() as conn:
            await self._expire_environment_operations(conn)
            await conn.execute(
                pg_insert(operations)
                .values(
                    id=UUID(identifier),
                    user_id=user_id,
                    conversation_id=UUID(str(conversation_id)),
                    request_id=request_id,
                    payload=change,
                    status="staged",
                    created_at=_now(),
                    expires_at=_now() + timedelta(hours=24),
                )
                .on_conflict_do_nothing()
            )
            row = (
                await conn.execute(
                    select(operations.c.id).where(
                        operations.c.user_id == user_id,
                        operations.c.conversation_id == UUID(str(conversation_id)),
                        operations.c.request_id == request_id,
                    )
                )
            ).scalar_one()
            if str(row) != identifier:
                raise EnvironmentConflict("相同请求不能提交不同的环境变量操作。")

    async def environment_operation(self, identifier, user_id, conversation_id, *, manifest=None):
        async with self.engine.begin() as conn:
            await self._expire_environment_operations(conn)
            row = (
                (
                    await conn.execute(
                        select(operations)
                        .where(
                            operations.c.id == UUID(identifier),
                            operations.c.user_id == user_id,
                            operations.c.conversation_id == UUID(conversation_id),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if not row or row["status"] in {"expired", "rejected"}:
                raise EnvironmentConflict("环境变量操作已失效，请重新输入。")
            row = dict(row)
            if manifest is not None and row["status"] == "staged":
                await conn.execute(
                    update(operations)
                    .where(operations.c.id == row["id"])
                    .values(manifest=manifest, status="prepared")
                )
                row.update(manifest=manifest, status="prepared")
            return row

    @resource_change(payload="change")
    async def commit_environment(self, identifier, user_id, conversation_id, manifest, change):
        try:
            async with self.engine.begin() as conn:
                row = (
                    (
                        await conn.execute(
                            select(operations)
                            .where(
                                operations.c.id == UUID(identifier),
                                operations.c.user_id == user_id,
                                operations.c.conversation_id == UUID(conversation_id),
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .first()
                )
                if not row or row["manifest"] != manifest:
                    raise EnvironmentConflict("审批清单不匹配。")
                if row["status"] == "committed":
                    if row["response"] is None:
                        raise EnvironmentConflict("操作回执已过期，不能再次执行。")
                    return row["response"]
                if (
                    row["status"] != "prepared"
                    or row["expires_at"] <= _now()
                    or row["payload"] != change
                ):
                    raise EnvironmentConflict("操作已失效，请重新输入。")
                result = await self._mutate_environment(conn, change)
                await conn.execute(
                    update(operations)
                    .where(operations.c.id == row["id"])
                    .values(payload=None, response=result, status="committed")
                )
                return result
        except IntegrityError:
            raise EnvironmentConflict("变量名已存在，请刷新后重试。") from None

    async def reject_environment(self, identifier, user_id, conversation_id):
        async with self.engine.begin() as conn:
            await conn.execute(
                update(operations)
                .where(
                    operations.c.id == UUID(identifier),
                    operations.c.user_id == user_id,
                    operations.c.conversation_id == UUID(str(conversation_id)),
                    operations.c.status.in_(["staged", "prepared"]),
                )
                .values(payload=None, status="rejected")
            )
