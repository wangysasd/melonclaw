"""Skill、MCP 与自定义模型配置的仓储：可见性查询、CRUD 和配置版本戳。

Skill 可见性规则（供应商统一共享，模型按 global/user 区分）：
- ``scope='global'`` 的行对所有用户可见，但受两层启停约束：
  管理员全员开关（``skills.enabled``）+ 用户个人偏好
  （``skill_user_states.enabled``，无行即默认启用）；
- ``scope='user'`` 的行仅创建者可见，启停由 ``skills.enabled`` 表达；
- 写操作权限（谁能改 global）在 services 层校验，
  这里只负责持久化和查询。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from melonclaw.core.config import provider_env_key
from melonclaw.core.model_catalog import DEFAULT_CONTEXT_WINDOW
from melonclaw.database.schema import (
    model_configs,
    model_providers,
    provider_user_keys,
    skill_user_states,
    skills,
)
from melonclaw.repository.mappers import _as_iso, _now


def _skill_row(
    *,
    name: str,
    scope: str,
    source_type: str,
    created_by: str,
    storage_path: str,
    enabled: bool,
) -> dict[str, Any]:
    """构造一行 skills 记录；插入路径共用，避免字段列表两处漂移。"""

    timestamp = _now()
    return {
        "id": uuid4(),
        "name": name,
        "scope": scope,
        "source_type": source_type,
        "created_by": created_by,
        "enabled": enabled,
        "storage_path": storage_path,
        "version": 1,
        "status": "ready",
        "content_hash": "",
        "source_url": "",
        "source_ref": "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


class ResourceRepositoryMixin:
    async def list_visible_skill_rows(
        self, user_id: str, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        """返回某用户可见的 Skill 行（global + 自己的 user scope）。

        每个结果附带 ``user_enabled``：共享 Skill 的个人偏好（无行即
        ``None``，表示默认启用）；私有 Skill 恒为 ``None``。
        共享行可见条件 = 全员开关开着 AND 个人偏好未关；私有行 =
        创建者是自己 AND 行级 enabled。管理界面传 ``include_disabled``
        拿到可管理的全集（含停用的共享项与私有项）。
        """

        query = (
            select(skills, skill_user_states.c.enabled.label("user_enabled"))
            .outerjoin(
                skill_user_states,
                (skill_user_states.c.skill_id == skills.c.id)
                & (skill_user_states.c.user_id == user_id),
            )
            .order_by(skills.c.name.asc())
        )
        if include_disabled:
            query = query.where(
                (skills.c.scope == "global") | (skills.c.created_by == user_id)
            )
        else:
            query = query.where(
                or_(
                    and_(
                        skills.c.scope == "global",
                        skills.c.enabled.is_(True),
                        or_(
                            skill_user_states.c.enabled.is_(None),
                            skill_user_states.c.enabled.is_(True),
                        ),
                    ),
                    and_(skills.c.scope == "user", skills.c.created_by == user_id, skills.c.enabled.is_(True)),
                )
            )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_skill_user_state(
        self, user_id: str, skill_id: Any
    ) -> bool | None:
        query = select(skill_user_states.c.enabled).where(
            (skill_user_states.c.user_id == user_id)
            & (skill_user_states.c.skill_id == skill_id)
        )
        async with self.engine.connect() as connection:
            value = (await connection.execute(query)).scalar()
        return bool(value) if value is not None else None

    async def set_skill_user_state(
        self, user_id: str, skill_id: Any, enabled: bool
    ) -> None:
        """写入个人启停偏好；同 (user_id, skill_id) 冲突时覆盖。"""

        timestamp = _now()
        statement = (
            pg_insert(skill_user_states)
            .values(
                user_id=user_id,
                skill_id=skill_id,
                enabled=enabled,
                created_at=timestamp,
                updated_at=timestamp,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "skill_id"],
                set_={"enabled": enabled, "updated_at": timestamp},
            )
        )
        async with self.engine.begin() as connection:
            await connection.execute(statement)

    async def list_skill_rows_by_name(self, name: str) -> list[dict[str, Any]]:
        """取该名字的全部行（全局共享 + 各用户私有），供导入查重与消歧。"""

        query = select(skills).where(skills.c.name == name)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_skill_row(self, skill_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                select(skills).where(skills.c.id == UUID(str(skill_id)))
            )).mappings().first()
        return dict(row) if row else None

    async def list_all_skill_rows(self) -> list[dict[str, Any]]:
        """返回全部 Skill 行，供索引重建核对目录是否还在。

        不做可见性过滤：要发现的是所有“有行无目录”的项，包含已停用的
        和属于其他用户的行。
        """

        query = select(skills).order_by(skills.c.name.asc())
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_skill_row(
        self,
        *,
        name: str,
        scope: str,
        source_type: str,
        created_by: str,
        storage_path: str,
        enabled: bool = True,
        skill_id: UUID | None = None,
        status: str = "ready",
        content_hash: str = "",
        source_url: str = "",
        source_ref: str = "",
    ) -> dict[str, Any]:
        row = _skill_row(
            name=name,
            scope=scope,
            source_type=source_type,
            created_by=created_by,
            storage_path=storage_path,
            enabled=enabled,
        )
        row.update(status=status, content_hash=content_hash, source_url=source_url, source_ref=source_ref)
        if skill_id is not None:
            row["id"] = skill_id
        async with self.engine.begin() as connection:
            await connection.execute(insert(skills).values(**row))
        return row

    async def create_skill_row_if_missing(
        self,
        *,
        name: str,
        scope: str,
        source_type: str,
        created_by: str,
        storage_path: str,
        enabled: bool = True,
    ) -> bool:
        """索引重建用：按唯一性规则补缺登记，返回是否真的插入。

        冲突目标随 scope 不同：global 行与全局共享名冲突，user 行与
        同一创建者的私有名冲突。已存在的行一律不覆盖——启用状态等运营
        字段由使用者通过资源管理 API 维护。
        """

        row = _skill_row(
            name=name,
            scope=scope,
            source_type=source_type,
            created_by=created_by,
            storage_path=storage_path,
            enabled=enabled,
        )
        if scope == "global":
            conflict = {
                "index_elements": ["name"],
                "index_where": text("scope = 'global'"),
            }
        else:
            conflict = {
                "index_elements": ["created_by", "name"],
                "index_where": text("scope = 'user'"),
            }
        statement = (
            pg_insert(skills)
            .values(**row)
            .on_conflict_do_nothing(**conflict)
            .returning(skills.c.name)
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(statement)
            return result.scalar_one_or_none() is not None

    async def update_skill_row(self, skill_id: Any, **fields: Any) -> None:
        fields["updated_at"] = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                update(skills).where(skills.c.id == skill_id).values(**fields)
            )

    async def delete_skill_row(self, skill_id: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(delete(skills).where(skills.c.id == skill_id))

    async def skills_revision(self) -> str:
        """全局 Skill 配置版本戳：Agent 缓存键用它做配置变更失效。"""

        query = select(func.max(skills.c.updated_at))
        async with self.engine.connect() as connection:
            value = (await connection.execute(query)).scalar()
        return _as_iso(value) if value is not None else ""

    # ---- 模型供应商 ----

    async def list_provider_rows(
        self, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        """返回共享供应商；供应商统一由管理员维护，对所有用户可见。"""

        query = select(model_providers)
        if not include_disabled:
            query = query.where(model_providers.c.enabled.is_(True))
        query = query.order_by(model_providers.c.provider_key.asc())
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_provider_row(self, provider_key: str) -> dict[str, Any] | None:
        query = select(model_providers).where(
            model_providers.c.provider_key == provider_key
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return dict(row) if row is not None else None

    async def create_provider_row(
        self,
        *,
        provider_key: str,
        scope: str,
        source_type: str,
        display_name: str,
        provider_type: str,
        base_url: str,
        api_key: str | None,
        models_endpoint: str | None,
        created_by: str,
        api_key_env: str = "",
        request_headers: dict[str, str] | None = None,
        extra_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        timestamp = _now()
        row = {
            "id": uuid4(),
            "provider_key": provider_key,
            "scope": scope,
            "source_type": source_type,
            "display_name": display_name,
            "provider_type": provider_type,
            "base_url": base_url,
            "api_key": api_key,
            "models_endpoint": models_endpoint,
            "api_key_env": api_key_env,
            "request_headers": request_headers if request_headers is not None else {},
            "extra_config": extra_config if extra_config is not None else {},
            "enabled": enabled,
            "created_by": created_by,
            "version": 1,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(model_providers).values(**row))
        return row

    async def update_provider_row(self, provider_key: str, **fields: Any) -> None:
        fields["updated_at"] = _now()
        # 任何字段更新都视为配置变更：版本自推进，Agent 缓存键随之失效。
        fields["version"] = model_providers.c.version + 1
        async with self.engine.begin() as connection:
            await connection.execute(
                update(model_providers)
                .where(model_providers.c.provider_key == provider_key)
                .values(**fields)
            )

    async def delete_provider_row(self, provider_key: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(model_providers).where(
                    model_providers.c.provider_key == provider_key
                )
            )

    async def get_user_provider_key(
        self, provider_key: str, user_id: str
    ) -> dict[str, Any] | None:
        """取某用户在某共享供应商上覆盖的 Key；无行返回 None。"""

        query = select(provider_user_keys).where(
            and_(
                provider_user_keys.c.provider_key == provider_key,
                provider_user_keys.c.user_id == user_id,
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return dict(row) if row is not None else None

    async def list_user_provider_keys(
        self, user_id: str
    ) -> dict[str, str]:
        """返回某用户全部 Key 覆盖：{provider_key: True} 只 exposed 有无。"""

        query = select(provider_user_keys).where(
            provider_user_keys.c.user_id == user_id
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return {str(row["provider_key"]): True for row in rows}

    async def upsert_user_provider_key(
        self, *, provider_key: str, user_id: str, api_key: str
    ) -> None:
        """新增或覆盖用户 Key；空 Key 由调用方拒绝，这里只存非空值。"""

        timestamp = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                pg_insert(provider_user_keys)
                .values(
                    id=uuid4(),
                    provider_key=provider_key,
                    user_id=user_id,
                    api_key=api_key,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                .on_conflict_do_update(
                    constraint="uq_provider_user_keys",
                    set_={"api_key": api_key, "updated_at": timestamp},
                )
            )

    async def delete_user_provider_key(
        self, provider_key: str, user_id: str
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(provider_user_keys).where(
                    and_(
                        provider_user_keys.c.provider_key == provider_key,
                        provider_user_keys.c.user_id == user_id,
                    )
                )
            )

    async def effective_provider_api_key(
        self, provider_row: dict[str, Any], user_id: str
    ) -> str | None:
        """用户覆盖 Key 优先，其次共享 Key；两者皆无返回 None。"""

        override = await self.get_user_provider_key(
            str(provider_row["provider_key"]), user_id
        )
        if override and str(override.get("api_key") or "").strip():
            return str(override["api_key"])
        shared = provider_row["api_key"]
        return str(shared) if shared else provider_env_key(provider_row["api_key_env"])

    async def list_models_of_provider(self, provider_key: str) -> list[dict[str, Any]]:
        query = select(model_configs).where(
            model_configs.c.provider_key == provider_key
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    # ---- 自定义模型 ----

    async def list_visible_model_rows(
        self, user_id: str, *, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        """返回某用户可用的自定义模型行（global + 自己的 user scope）。"""

        query = select(model_configs).where(
            (model_configs.c.scope == "global")
            | (model_configs.c.created_by == user_id)
        )
        if not include_disabled:
            query = query.where(model_configs.c.enabled.is_(True))
        query = query.order_by(model_configs.c.model_key.asc())
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_model_row(self, model_key: str) -> dict[str, Any] | None:
        query = select(model_configs).where(
            model_configs.c.model_key == model_key
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return dict(row) if row is not None else None

    async def get_model_with_provider(
        self, model_key: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """取出模型行及其供应商行；解析运行时模型配置的专用读取。"""

        query = (
            select(model_configs, model_providers)
            .where(model_configs.c.model_key == model_key)
            .where(
                model_configs.c.provider_key
                == model_providers.c.provider_key
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        model_row = {
            column.name: row[model_configs.c[column.name]]
            for column in model_configs.c
        }
        provider_row = {
            column.name: row[model_providers.c[column.name]]
            for column in model_providers.c
        }
        return model_row, provider_row

    async def create_model_row(
        self,
        *,
        model_key: str,
        provider_key: str,
        scope: str,
        source_type: str,
        display_name: str,
        model_name: str,
        created_by: str,
        enabled: bool = True,
        is_default: bool = False,
        input_modalities: list[str] | None = None,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ) -> dict[str, Any]:
        timestamp = _now()
        row = {
            "id": uuid4(),
            "model_key": model_key,
            "provider_key": provider_key,
            "scope": scope,
            "source_type": source_type,
            "display_name": display_name,
            "model_name": model_name,
            "enabled": enabled,
            "is_default": is_default,
            "input_modalities": list(input_modalities or ["text"]),
            "context_window": context_window,
            "created_by": created_by,
            "version": 1,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(model_configs).values(**row))
        return row

    async def set_default_model(self, model_key: str) -> None:
        """切换默认模型：单事务内清掉旧默认再置新默认。

        目标行 ``updated_at`` 自推，``models_revision`` 随之变化，
        Agent 缓存键自动失效；旧默认行不必 bump 版本。
        """

        async with self.engine.begin() as connection:
            # 串行化默认切换，避免同一归属下并发设置撞唯一索引。
            await connection.execute(select(model_configs.c.model_key).with_for_update())
            target = (await connection.execute(
                select(model_configs).where(model_configs.c.model_key == model_key)
            )).mappings().one()
            scope_filter = model_configs.c.scope == target["scope"]
            if target["scope"] == "user":
                scope_filter = and_(scope_filter, model_configs.c.created_by == target["created_by"])
            await connection.execute(
                update(model_configs).where(scope_filter).values(is_default=False)
            )
            await connection.execute(
                update(model_configs)
                .where(model_configs.c.model_key == model_key)
                .values(is_default=True, updated_at=_now())
            )

    async def update_model_row(self, model_key: str, **fields: Any) -> None:
        fields["updated_at"] = _now()
        # 任何字段更新都视为配置变更：版本自推进，Agent 缓存键随之失效。
        fields["version"] = model_configs.c.version + 1
        async with self.engine.begin() as connection:
            await connection.execute(
                update(model_configs)
                .where(model_configs.c.model_key == model_key)
                .values(**fields)
            )

    async def delete_model_row(self, model_key: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(model_configs).where(
                    model_configs.c.model_key == model_key
                )
            )

    async def models_revision(self) -> str:
        """模型与供应商任一张表变更都会推进版本戳，Agent 缓存随之失效。"""

        model_query = select(func.max(model_configs.c.updated_at))
        provider_query = select(func.max(model_providers.c.updated_at))
        async with self.engine.connect() as connection:
            model_value = (await connection.execute(model_query)).scalar()
            provider_value = (await connection.execute(provider_query)).scalar()
        values = [value for value in (model_value, provider_value) if value is not None]
        return _as_iso(max(values)) if values else ""
