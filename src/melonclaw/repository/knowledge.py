"""知识库 SQL、授权与原子变更。锁顺序：用户→库→文档→版本→job。"""

from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import and_, case, delete, func, insert, or_, select, update

from melonclaw.core.knowledge import (
    LIBRARY_EVENT_ACTIONS,
    KnowledgeConflict,
    KnowledgeDenied,
    KnowledgeError,
    KnowledgeForbidden,
    is_system_admin,
    knowledge_operation_item,
    moderation_reason,
    require_role,
)
from melonclaw.database.knowledge_schema import EVENT_ACTIONS
from melonclaw.database.schema import (
    KNOWLEDGE_TABLES,
    tenants,
    users,
)
from melonclaw.repository.mappers import _now

bases, members, documents, revisions, texts, chunks, jobs, events = KNOWLEDGE_TABLES


SHARE_SCOPES = {"everyone", "tenant", "selected"}
EVENT_DETAIL_KEYS = {
    "base_created": set(),
    "document_deleted": set(),
    "revision_published": set(),
    "base_updated": {"fields", "version"},
    "base_deleted": {"reason"},
    "base_suspended": {"reason"},
    "base_restored": {"reason"},
    "member_granted": {"role"},
    "member_updated": {"role"},
    "member_revoked": set(),
    "ownership_transferred": {"previous_owner", "new_owner"},
    "share_scope_changed": {"previous", "share_scope"},
}
_MODERATION_STATUS = {
    "base_suspended": "active",
    "base_restored": "suspended",
    "base_deleted": "suspended",
}


def visible_query(user_id):
    """所有者、点名成员，以及共享范围里的查看者。同租户按所有者当前租户计算。"""

    actor, owner = users.alias("actor"), users.alias("owner")
    actor_tenant, owner_tenant = tenants.alias("actor_tenant"), tenants.alias("owner_tenant")
    membership = members.alias("membership")
    system_admin = and_(actor.c.user_id == "admin", actor.c.tenant_id == "system")
    role = case(
        (bases.c.owner_user_id == user_id, "owner"),
        (system_admin, "system_admin"),
        (membership.c.role.is_not(None), membership.c.role),
        else_="viewer",
    ).label("role")
    return (
        select(bases, role)
        .select_from(
            bases.join(owner, owner.c.user_id == bases.c.owner_user_id)
            .join(owner_tenant, owner_tenant.c.tenant_id == owner.c.tenant_id)
            .join(actor, actor.c.user_id == user_id)
            .join(actor_tenant, actor_tenant.c.tenant_id == actor.c.tenant_id)
            .outerjoin(
                membership, and_(membership.c.kb_id == bases.c.id, membership.c.user_id == user_id)
            )
        )
        .where(
            actor.c.tenant_status == "active",
            actor_tenant.c.enabled.is_(True),
            or_(system_admin, and_(owner.c.tenant_status == "active", owner_tenant.c.enabled.is_(True))),
            # 停用和删除不从通用查询放行。管理员查看已停用库走 suspended_admin_query。
            bases.c.status == "active",
            or_(
                bases.c.owner_user_id == user_id,
                system_admin,
                membership.c.role.is_not(None),
                bases.c.share_scope == "everyone",
                and_(bases.c.share_scope == "tenant", actor.c.tenant_id == owner.c.tenant_id),
            ),
        )
        )


def suspended_admin_query(user_id):
    """仅 system/admin 可见的已停用库元数据。不授予文档、检索或下载。"""

    actor, owner = users.alias("actor"), users.alias("owner")
    actor_tenant = tenants.alias("actor_tenant")
    return (
        select(
            bases.c.id,
            bases.c.name,
            bases.c.description,
            bases.c.status,
            bases.c.share_scope,
            bases.c.version,
            bases.c.owner_user_id,
            bases.c.created_at,
            bases.c.updated_at,
            owner.c.user_name_zh.label("owner_name_zh"),
        )
        .select_from(
            bases.join(owner, owner.c.user_id == bases.c.owner_user_id)
            .join(actor, actor.c.user_id == user_id)
            .join(actor_tenant, actor_tenant.c.tenant_id == actor.c.tenant_id)
        )
        .where(
            actor.c.user_id == "admin",
            actor.c.tenant_id == "system",
            actor.c.tenant_status == "active",
            actor_tenant.c.enabled.is_(True),
            bases.c.status == "suspended",
        )
    )


def _system_admin(actor) -> bool:
    return bool(actor) and is_system_admin(actor.user_id, actor.tenant_id)


def _moderation_status_message(action, status):
    if action == "base_restored" and status == "deleted":
        return "已删除的知识库不能恢复。"
    if action == "base_restored":
        return "只能恢复已停用的知识库。"
    if action == "base_suspended":
        return "只能停用使用中的知识库。"
    return "只能删除已停用的知识库。"


def validate_knowledge_event(action, details):
    if set(EVENT_DETAIL_KEYS) != set(EVENT_ACTIONS):
        raise KnowledgeError("不允许的审计字段。")
    if action not in EVENT_DETAIL_KEYS or set(details) - EVENT_DETAIL_KEYS[action]:
        raise KnowledgeError("不允许的审计字段。")
    if action in {"base_suspended", "base_restored"} and "reason" not in details:
        raise KnowledgeError("处置原因无效。")
    if "reason" in details:
        moderation_reason(action, details["reason"])
    if "role" in details and details["role"] not in {"viewer", "editor"}:
        raise KnowledgeError("审计角色无效。")
    if ("share_scope" in details or "previous" in details) and (
        details.get("share_scope") not in SHARE_SCOPES or details.get("previous") not in SHARE_SCOPES
    ):
        raise KnowledgeError("审计共享范围无效。")
    if "fields" in details and (
        not isinstance(details["fields"], list)
        or any(f not in {"name", "description", "chunk_config", "share_scope"} for f in details["fields"])
        or len(details["fields"]) > 4
    ):
        raise KnowledgeError("审计字段列表无效。")
    if "version" in details and (type(details["version"]) is not int or details["version"] <= 0):
        raise KnowledgeError("审计版本无效。")
    for key in ("previous_owner", "new_owner"):
        if key in details and (not isinstance(details[key], str) or not 1 <= len(details[key]) <= 64):
            raise KnowledgeError("审计账户标识无效。")


class KnowledgeRepositoryMixin:
    async def knowledge_list(self, user_id, *, page, page_size, search):
        query = visible_query(user_id).where(bases.c.name.ilike("%" + search + "%"))
        async with self.engine.begin() as conn:
            # 两条查询使用同一快照，计数和分页口径保持一致。
            await conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            total = await conn.scalar(select(func.count()).select_from(query.subquery()))
            rows = (
                (
                    await conn.execute(
                        query.order_by(bases.c.updated_at.desc(), bases.c.id)
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    async def knowledge_detail(self, user_id, kb_id):
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(visible_query(user_id).where(bases.c.id == kb_id)))
                .mappings()
                .first()
            )
            if not row:
                raise KnowledgeDenied("知识库不存在或无权访问。")
            return dict(row)

    async def knowledge_operation_history(self, user_id, kb_id, *, page, page_size):
        """按库读取已有事件。只接受当前请求核验过的系统管理员，不返回正文。"""

        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            actor = await self.get_user_context(user_id, connection=conn)
            if not _system_admin(actor):
                raise KnowledgeForbidden("只有系统管理员可以查看知识库操作记录。")
            base = (
                await conn.execute(
                    select(bases.c.id, bases.c.name, bases.c.status).where(bases.c.id == kb_id)
                )
            ).mappings().first()
            if base is None:
                raise KnowledgeDenied("知识库不存在。")
            filtered = events.c.kb_id == kb_id
            filtered = and_(filtered, events.c.action.in_(LIBRARY_EVENT_ACTIONS))
            total = await conn.scalar(
                select(func.count()).select_from(events).where(filtered)
            )
            rows = (
                await conn.execute(
                    select(
                        events.c.id,
                        events.c.kb_id,
                        events.c.actor_user_id,
                        events.c.action,
                        events.c.target_id,
                        events.c.safe_details,
                        events.c.created_at,
                        users.c.user_name_zh,
                    )
                    .select_from(
                        events.outerjoin(users, users.c.user_id == events.c.actor_user_id)
                    )
                    .where(filtered)
                    .order_by(events.c.created_at.desc(), events.c.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).mappings().all()
        return {
            "kb_id": base["id"],
            "name": base["name"],
            "status": base["status"],
            "items": [
                knowledge_operation_item(dict(row), kb_name=base["name"]) for row in rows
            ],
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
        }

    async def _knowledge_owner_tenant(self, conn, base):
        tenant_id = await conn.scalar(select(users.c.tenant_id).where(users.c.user_id == base["owner_user_id"]))
        if tenant_id is None:
            raise KnowledgeDenied("知识库不存在或无权访问。")
        return tenant_id

    async def knowledge_published_document_count(self, user_id, kb_id):
        """已发布且仍是当前版本的资料份数，供升级前说明整理范围。"""

        async with self.engine.connect() as conn:
            visible = (
                (await conn.execute(visible_query(user_id).where(bases.c.id == kb_id)))
                .mappings()
                .first()
            )
            if not visible:
                raise KnowledgeDenied("知识库不存在或无权访问。")
            total = await conn.scalar(
                select(func.count())
                .select_from(
                    documents.join(
                        revisions,
                        and_(
                            revisions.c.kb_id == documents.c.kb_id,
                            revisions.c.id == documents.c.active_revision_id,
                        ),
                    )
                )
                .where(
                    documents.c.kb_id == kb_id,
                    documents.c.deleted_at.is_(None),
                    revisions.c.published_at.is_not(None),
                )
            )
            return int(total or 0)

    @asynccontextmanager
    async def knowledge_transaction(self, user_id, kb_id=None, target_id=None, target_ids=()):
        async with self.engine.begin() as conn:
            owner_id = user_id
            if kb_id:
                owner_id = await conn.scalar(
                    select(bases.c.owner_user_id).where(bases.c.id == kb_id)
                )
                if owner_id is None:
                    raise KnowledgeDenied("知识库不存在或无权访问。")
            ids = sorted({user_id, owner_id} | ({target_id} if target_id else set()) | set(target_ids))
            await conn.execute(
                select(users.c.user_id)
                .where(users.c.user_id.in_(ids))
                .order_by(users.c.user_id)
                .with_for_update()
            )
            actor = await self.get_user_context(user_id, connection=conn)
            owner = await self.get_user_context(owner_id, connection=conn)
            if not actor or (not owner and not (actor.user_id == "admin" and actor.tenant_id == "system")):
                raise KnowledgeDenied("知识库不存在或无权访问。")
            row = None
            if kb_id:
                row = (
                    (await conn.execute(select(bases).where(bases.c.id == kb_id).with_for_update()))
                    .mappings()
                    .one()
                )
                if row["owner_user_id"] != owner_id:
                    raise KnowledgeConflict("所有权已变化，请刷新重试。")
                visible = (
                    (await conn.execute(visible_query(user_id).where(bases.c.id == kb_id)))
                    .mappings()
                    .first()
                )
                if not visible:
                    raise KnowledgeDenied("知识库不存在或无权访问。")
                row = dict(visible)
            if target_id:
                target = await self.get_user_context(target_id, connection=conn)
                if not target:
                    raise KnowledgeDenied("目标用户不存在或已停用。")
            yield conn, row

    async def _knowledge_event(
        self, conn, kb_id, actor, action, target_type="base", target_id=None, **details
    ):
        # 只有角色、版本、原因代码和受影响字段名进入审计；禁止传请求对象或正文。
        validate_knowledge_event(action, details)
        await conn.execute(
            insert(events).values(
                id=uuid4(),
                kb_id=kb_id,
                actor_user_id=actor,
                action=action,
                target_type=target_type,
                target_id=str(target_id or kb_id),
                safe_details=details,
                created_at=_now(),
            )
        )

    async def knowledge_create(self, user_id, values):
        values = dict(values)
        selected = values.pop("selected_user_ids", [])
        async with self.knowledge_transaction(user_id, target_ids=selected) as (conn, _):
            await self._model_catalog_lock(conn)
            embedding = values.pop("embedding_model_id")
            await self.assert_fixed_embedding_reference(conn, embedding)
            await self._knowledge_unique_name(conn, user_id, values["name"])
            kb_id = uuid4()
            await conn.execute(
                insert(bases).values(
                    id=kb_id,
                    owner_user_id=user_id,
                    embedding_model_id=embedding,
                    version=1,
                    created_at=_now(),
                    updated_at=_now(),
                    **values,
                )
            )
            await self._knowledge_event(conn, kb_id, user_id, "base_created")
            if values.get("share_scope") == "selected":
                await self._knowledge_save_viewers(conn, kb_id, user_id, user_id, selected)
        return await self.knowledge_detail(user_id, kb_id)

    async def _knowledge_unique_name(self, conn, owner, name, exclude=None):
        query = select(bases.c.id).where(
            bases.c.owner_user_id == owner, bases.c.status != "deleted", bases.c.name == name
        )
        if exclude:
            query = query.where(bases.c.id != exclude)
        if await conn.scalar(query):
            raise KnowledgeConflict("你已有同名知识库。")

    async def knowledge_update(self, user_id, kb_id, version, values):
        values = dict(values)
        selected = values.pop("selected_user_ids", None)
        if values.keys() - {"name", "description", "chunk_config", "share_scope"}:
            raise KnowledgeError("知识库只允许修改名称、描述和分块配置。")
        if "share_scope" in values and values["share_scope"] not in SHARE_SCOPES:
            raise KnowledgeError("共享范围无效。")
        async with self.knowledge_transaction(user_id, kb_id, target_ids=selected or ()) as (conn, row):
            require_role(row["role"], "edit")
            if row["version"] != version:
                raise KnowledgeConflict("知识库已变化，请刷新重试。")
            if "name" in values:
                await self._knowledge_unique_name(conn, row["owner_user_id"], values["name"], kb_id)
            await conn.execute(
                update(bases)
                .where(bases.c.id == kb_id)
                .values(**values, version=bases.c.version + 1, updated_at=_now())
            )
            await self._knowledge_event(
                conn, kb_id, user_id, "base_updated", fields=sorted(values), version=version + 1
            )
            if "share_scope" in values and values["share_scope"] != row["share_scope"]:
                await self._knowledge_event(conn, kb_id, user_id, "share_scope_changed",
                    previous=row["share_scope"], share_scope=values["share_scope"])
            if selected is not None and values.get("share_scope", row["share_scope"]) == "selected":
                await self._knowledge_save_viewers(conn, kb_id, user_id, row["owner_user_id"], selected)
        return await self.knowledge_detail(user_id, kb_id)

    async def _knowledge_save_viewers(self, conn, kb_id, actor_id, owner_id, selected):
        wanted = set(selected)
        if owner_id in wanted or len(wanted) != len(selected):
            raise KnowledgeError("指定成员无效。")
        for target in wanted:
            if not await self.get_user_context(target, connection=conn):
                raise KnowledgeDenied("目标用户不存在或已停用。")
        current = (await conn.execute(select(members.c.user_id, members.c.role).where(members.c.kb_id == kb_id))).all()
        existing = {user: role for user, role in current}
        for target, role in existing.items():
            if role == "viewer" and target not in wanted:
                await conn.execute(delete(members).where(members.c.kb_id == kb_id, members.c.user_id == target))
                await self._knowledge_cancel_member_jobs(conn, kb_id, target)
                await self._knowledge_event(conn, kb_id, actor_id, "member_revoked", "member", target)
        for target in sorted(wanted - existing.keys()):
            await conn.execute(insert(members).values(kb_id=kb_id, user_id=target, role="viewer", granted_by=actor_id, created_at=_now(), updated_at=_now()))
            await self._knowledge_event(conn, kb_id, actor_id, "member_granted", "member", target, role="viewer")

    async def knowledge_user_candidates(self, user_id, *, search=""):
        async with self.engine.connect() as conn:
            if not await self.get_user_context(user_id, connection=conn):
                raise KnowledgeDenied("当前用户不可用。")
            query = (select(users.c.user_id, users.c.user_name_zh, tenants.c.tenant_name_zh)
                     .join(tenants, tenants.c.tenant_id == users.c.tenant_id)
                     .where(users.c.tenant_status == "active", tenants.c.enabled.is_(True), users.c.user_id != user_id,
                            or_(users.c.user_name_zh.ilike("%" + search + "%"), users.c.user_id.ilike("%" + search + "%"), tenants.c.tenant_name_zh.ilike("%" + search + "%")))
                     .order_by(users.c.user_id).limit(50))
            return {"items": [dict(row) for row in (await conn.execute(query)).mappings()]}

    async def _knowledge_cancel_jobs(self, conn, kb_ids, *, requested_by=None):
        condition = jobs.c.kb_id.in_(kb_ids)
        if requested_by is not None:
            condition = or_(condition, jobs.c.requested_by == requested_by)
        doc_ids = select(jobs.c.document_id).where(
            condition, jobs.c.status.in_(["queued", "running"])
        )
        await conn.execute(
            select(documents.c.id)
            .where(documents.c.id.in_(doc_ids))
            .order_by(documents.c.id)
            .with_for_update()
        )
        rev_ids = select(jobs.c.revision_id).where(
            condition, jobs.c.status.in_(["queued", "running"])
        )
        await conn.execute(
            select(revisions.c.id)
            .where(revisions.c.id.in_(rev_ids))
            .order_by(revisions.c.id)
            .with_for_update()
        )
        await conn.execute(
            select(jobs.c.id)
            .where(condition, jobs.c.status.in_(["queued", "running"]))
            .order_by(jobs.c.id)
            .with_for_update()
        )
        await conn.execute(
            update(jobs)
            .where(condition, jobs.c.status.in_(["queued", "running"]))
            .values(status="cancelled", run_token=None, updated_at=_now(), finished_at=_now())
        )
        await conn.execute(update(bases).where(bases.c.active_pipeline_job_id.in_(
            select(jobs.c.id).where(condition, jobs.c.status == "cancelled", jobs.c.started_at.is_(None))))
            .values(active_pipeline_job_id=None))


    async def knowledge_delete(self, user_id, kb_id, version):
        async with self.knowledge_transaction(user_id, kb_id) as (conn, row):
            require_role(row["role"], "delete")
            if row["version"] != version:
                raise KnowledgeConflict("知识库已变化，请刷新重试。")
            await self._knowledge_cancel_jobs(conn, [kb_id])
            await conn.execute(
                update(bases)
                .where(bases.c.id == kb_id)
                .values(
                    status="deleted",
                    deleted_at=_now(),
                    updated_at=_now(),
                    version=bases.c.version + 1,
                )
            )
            await self._knowledge_event(conn, kb_id, user_id, "base_deleted")

    @asynccontextmanager
    async def knowledge_moderation_transaction(self, user_id, kb_id):
        async with self.engine.begin() as conn:
            actor = await self.get_user_context(user_id, connection=conn)
            if not _system_admin(actor):
                raise KnowledgeDenied("知识库不存在或无权访问。")
            owner_id = await conn.scalar(select(bases.c.owner_user_id).where(bases.c.id == kb_id))
            if owner_id is None:
                raise KnowledgeDenied("知识库不存在或无权访问。")
            await conn.execute(
                select(users.c.user_id)
                .where(users.c.user_id.in_(sorted({user_id, owner_id})))
                .order_by(users.c.user_id)
                .with_for_update()
            )
            actor = await self.get_user_context(user_id, connection=conn)
            if not _system_admin(actor):
                raise KnowledgeDenied("知识库不存在或无权访问。")
            row = (
                await conn.execute(select(bases).where(bases.c.id == kb_id).with_for_update())
            ).mappings().one()
            if row["owner_user_id"] != owner_id:
                raise KnowledgeConflict("所有权已变化，请刷新重试。")
            yield conn, dict(row)

    async def _knowledge_moderate(self, user_id, kb_id, version, reason, action):
        moderation_reason(action, reason)
        expected = _MODERATION_STATUS[action]
        if type(version) is not int or version <= 0:
            raise KnowledgeError("知识库版本无效。")
        async with self.knowledge_moderation_transaction(user_id, kb_id) as (conn, row):
            if row["version"] != version:
                raise KnowledgeConflict("知识库已变化，请刷新重试。")
            if row["status"] != expected:
                raise KnowledgeConflict(_moderation_status_message(action, row["status"]))
            if action in {"base_suspended", "base_deleted"}:
                await self._knowledge_cancel_jobs(conn, [kb_id])
            now = _now()
            if action == "base_suspended":
                status = "suspended"
                extra = {}
            elif action == "base_restored":
                status = "active"
                extra = {}
            else:
                status = "deleted"
                extra = {"deleted_at": now}
            await conn.execute(
                update(bases)
                .where(bases.c.id == kb_id)
                .values(status=status, updated_at=now, version=bases.c.version + 1, **extra)
            )
            await self._knowledge_event(conn, kb_id, user_id, action, reason=reason)
        return {"id": kb_id, "status": status, "version": version + 1}

    async def knowledge_suspend(self, user_id, kb_id, version, reason):
        return await self._knowledge_moderate(user_id, kb_id, version, reason, "base_suspended")

    async def knowledge_restore(self, user_id, kb_id, version, reason):
        return await self._knowledge_moderate(user_id, kb_id, version, reason, "base_restored")

    async def knowledge_dispose(self, user_id, kb_id, version, reason):
        return await self._knowledge_moderate(user_id, kb_id, version, reason, "base_deleted")

    async def knowledge_moderation_list(self, user_id, *, page, page_size, search):
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            actor = await self.get_user_context(user_id, connection=conn)
            if not _system_admin(actor):
                raise KnowledgeDenied("知识库不存在或无权访问。")
            query = suspended_admin_query(user_id).where(bases.c.name.ilike("%" + search + "%"))
            total = await conn.scalar(select(func.count()).select_from(query.subquery()))
            rows = (
                await conn.execute(
                    query.order_by(bases.c.updated_at.desc(), bases.c.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).mappings().all()
        return {
            "items": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "status": row["status"],
                    "share_scope": row["share_scope"],
                    "version": row["version"],
                    "owner_user_id": row["owner_user_id"],
                    "owner_name_zh": row["owner_name_zh"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def knowledge_members(self, user_id, kb_id, *, candidates=False, search=""):
        async with self.knowledge_transaction(user_id, kb_id) as (conn, row):
            require_role(row["role"], "members")
            if candidates:
                query = (
                    select(users.c.user_id, users.c.user_name_zh, tenants.c.tenant_name_zh)
                    .join(tenants, tenants.c.tenant_id == users.c.tenant_id)
                    .where(
                        users.c.tenant_status == "active",
                        tenants.c.enabled.is_(True),
                        users.c.user_id != row["owner_user_id"],
                        or_(
                            users.c.user_name_zh.ilike("%" + search + "%"),
                            users.c.user_id.ilike("%" + search + "%"),
                            tenants.c.tenant_name_zh.ilike("%" + search + "%"),
                        ),
                    )
                    .order_by(users.c.user_id)
                    .limit(50)
                )
            else:
                query = (
                    select(members.c.user_id, members.c.role, users.c.user_name_zh, tenants.c.tenant_name_zh)
                    .join(users, users.c.user_id == members.c.user_id)
                    .join(tenants, tenants.c.tenant_id == users.c.tenant_id)
                    .where(members.c.kb_id == kb_id)
                    .order_by(members.c.user_id)
                )
            return {"items": [dict(r) for r in (await conn.execute(query)).mappings()]}

    async def knowledge_member_save(self, user_id, kb_id, target_id, role):
        async with self.knowledge_transaction(user_id, kb_id, target_id) as (conn, row):
            existing = (
                (
                    await conn.execute(
                        select(members).where(
                            members.c.kb_id == kb_id, members.c.user_id == target_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            require_role(row["role"], "members")
            if role not in {None, "viewer", "editor"}:
                raise KnowledgeError("成员角色无效。")
            if target_id == row["owner_user_id"]:
                raise KnowledgeError("所有者不能重复添加为成员。")
            if role is None:
                await conn.execute(
                    delete(members).where(members.c.kb_id == kb_id, members.c.user_id == target_id)
                )
                # 撤权后不能继续发布该用户请求的作业。
                await self._knowledge_cancel_member_jobs(conn, kb_id, target_id)
                action = "member_revoked"
            elif existing:
                await conn.execute(
                    update(members)
                    .where(members.c.kb_id == kb_id, members.c.user_id == target_id)
                    .values(role=role, granted_by=user_id, updated_at=_now())
                )
                if role == "viewer":
                    await self._knowledge_cancel_member_jobs(conn, kb_id, target_id)
                action = "member_updated"
            else:
                await conn.execute(
                    insert(members).values(
                        kb_id=kb_id,
                        user_id=target_id,
                        role=role,
                        granted_by=user_id,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
                action = "member_granted"
            await self._knowledge_event(
                conn,
                kb_id,
                user_id,
                action,
                "member",
                target_id,
                **({"role": role} if role else {}),
            )

    async def _knowledge_cancel_member_jobs(self, conn, kb_id, target_id):
        # 与全库取消共用锁协议，但仅选取该成员发起的本库作业。
        ids = (
            (
                await conn.execute(
                    select(jobs.c.id).where(
                        jobs.c.kb_id == kb_id,
                        jobs.c.requested_by == target_id,
                        jobs.c.status.in_(["queued", "running"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return
        await conn.execute(
            select(documents.c.id)
            .where(documents.c.id.in_(select(jobs.c.document_id).where(jobs.c.id.in_(ids))))
            .order_by(documents.c.id)
            .with_for_update()
        )
        await conn.execute(
            select(revisions.c.id)
            .where(revisions.c.id.in_(select(jobs.c.revision_id).where(jobs.c.id.in_(ids))))
            .order_by(revisions.c.id)
            .with_for_update()
        )
        await conn.execute(
            select(jobs.c.id).where(jobs.c.id.in_(ids)).order_by(jobs.c.id).with_for_update()
        )
        await conn.execute(
            update(jobs)
            .where(jobs.c.id.in_(ids), jobs.c.status.in_(["queued", "running"]))
            .values(status="cancelled", run_token=None, finished_at=_now(), updated_at=_now())
        )
        await conn.execute(update(bases).where(bases.c.active_pipeline_job_id.in_(
            select(jobs.c.id).where(jobs.c.id.in_(ids), jobs.c.started_at.is_(None))))
            .values(active_pipeline_job_id=None))


    async def knowledge_set_share_scope(self, user_id, kb_id, version, share_scope):
        if share_scope not in SHARE_SCOPES:
            raise KnowledgeError("共享范围无效。")
        async with self.knowledge_transaction(user_id, kb_id) as (conn, row):
            require_role(row["role"], "members")
            if version != row["version"]:
                raise KnowledgeConflict("知识库已变化，请刷新重试。")
            if row["share_scope"] == share_scope:
                return dict(row)
            await conn.execute(
                update(bases)
                .where(bases.c.id == kb_id)
                .values(share_scope=share_scope, updated_at=_now(), version=bases.c.version + 1)
            )
            await self._knowledge_event(
                conn,
                kb_id,
                user_id,
                "share_scope_changed",
                previous=row["share_scope"],
                share_scope=share_scope,
            )
        return await self.knowledge_detail(user_id, kb_id)

    async def knowledge_transfer(self, user_id, kb_id, target_id, version):
        async with self.knowledge_transaction(user_id, kb_id, target_id) as (conn, row):
            require_role(row["role"], "transfer")
            if target_id == user_id:
                raise KnowledgeError("请选择其他有效用户。")
            if version != row["version"]:
                raise KnowledgeConflict("知识库已变化，请刷新重试。")
            await self._knowledge_unique_name(conn, target_id, row["name"])
            await conn.execute(
                delete(members).where(members.c.kb_id == kb_id, members.c.user_id == target_id)
            )
            await conn.execute(
                insert(members).values(
                    kb_id=kb_id,
                    user_id=user_id,
                    role="editor",
                    granted_by=user_id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            await conn.execute(
                update(bases)
                .where(bases.c.id == kb_id)
                .values(owner_user_id=target_id, updated_at=_now(), version=bases.c.version + 1)
            )
            await self._knowledge_event(
                conn,
                kb_id,
                user_id,
                "ownership_transferred",
                previous_owner=user_id,
                new_owner=target_id,
            )

    async def knowledge_account_change(self, conn, user_id, *, deleting=False, suspending=False):
        # account_guard 独占锁在所有业务 API 的共享锁前串行化；用户锁仍遵循统一顺序。
        shared_owners = (
            (
                await conn.execute(
                    select(bases.c.owner_user_id).where(
                        bases.c.id.in_(select(members.c.kb_id).where(members.c.user_id == user_id))
                    )
                )
            )
            .scalars()
            .all()
        )
        participant_ids = sorted({user_id, *shared_owners})
        await conn.execute(
            select(users.c.user_id)
            .where(users.c.user_id.in_(participant_ids))
            .order_by(users.c.user_id)
            .with_for_update()
        )
        owned = (
            (
                await conn.execute(
                    select(bases)
                    .where(bases.c.owner_user_id == user_id)
                    .order_by(bases.c.id)
                )
            )
            .mappings()
            .all()
        )
        shared_ids = select(members.c.kb_id).where(members.c.user_id == user_id)
        await conn.execute(
            select(bases.c.id)
            .where(or_(bases.c.owner_user_id == user_id, bases.c.id.in_(shared_ids)))
            .order_by(bases.c.id)
            .with_for_update()
        )
        if not deleting and not suspending:
            # 换租户保留点名授权。同租户和所有人按所有者当前租户重新计算。
            return
        if deleting and any(row["status"] != "deleted" for row in owned):
            raise KnowledgeConflict("请先转让或删除该用户拥有的知识库。")
        if suspending:
            await self._knowledge_cancel_jobs(conn, [r["id"] for r in owned], requested_by=user_id)
            return
        grants = (
            (
                await conn.execute(
                    select(members).where(
                        or_(
                            members.c.user_id == user_id,
                            members.c.kb_id.in_([r["id"] for r in owned]),
                        )
                    )
                )
            )
            .mappings()
            .all()
        )
        for grant in grants:
            await self._knowledge_event(
                conn, grant["kb_id"], None, "member_revoked", "member", grant["user_id"]
            )
        await conn.execute(
            delete(members).where(
                or_(members.c.user_id == user_id, members.c.kb_id.in_([r["id"] for r in owned]))
            )
        )
        await self._knowledge_cancel_jobs(conn, [r["id"] for r in owned], requested_by=user_id)


