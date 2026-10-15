"""聊天与后台任务共用数据库模型及当前凭据解析；不初始化聊天/Memory。"""

from typing import Any

from melonclaw.core.model_catalog import (
    ResolvedModel,
    custom_model_id,
    is_custom_model_id,
    resolve_model_row,
)


class ModelResolutionMixin:
    async def resolve_system_default_model(self, user_id: str, *, connection=None) -> ResolvedModel:
        """仅解析管理员的全局默认，不采用个人默认、聊天选择或第一条可用行。"""
        key = await self.require_ready().get_system_default_model_key(connection=connection)
        if key is None:
            raise ValueError("管理员尚未设置系统默认模型，请先设置或明确指定执行模型。")
        try:
            return await self.resolve_model(user_id, custom_model_id(key), connection=connection)
        except ValueError as exc:
            raise ValueError("系统默认模型已停用或凭据不可用，请联系管理员配置，或明确指定执行模型。") from exc

    async def _model_api_key(
        self, row: dict[str, Any], provider: dict[str, Any], user_id: str, *, connection=None
    ) -> str | None:
        """个人模型只能使用本人的 Key；全局模型保留个人覆盖规则。"""
        storage = self.require_ready()
        if row["scope"] == "user":
            personal = await storage.get_user_provider_key(
                str(provider["provider_key"]), user_id, connection=connection
            )
            return str(personal["api_key"]) if personal else None
        return await storage.effective_provider_api_key(provider, user_id, connection=connection)

    async def resolve_model(
        self,
        user_id: str,
        model_id: str | None = None,
        *,
        connection=None,
    ) -> ResolvedModel:
        """按用户解析模型：统一从 model_configs 表查询。

        - ``model_id=None``：取默认行（is_default 的可见启用行），无则
          第一条可见有 Key 的行，再无则提示先配置模型；
        - ``custom:`` 前缀：行不存在、不可见或已停用时报错；
        - 其他 ID：报错，模型目录只认数据库行。
        """

        if self.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        if model_id and is_custom_model_id(model_id):
            model_key = model_id.removeprefix("custom:")
            storage = self.require_ready()
            pair = await storage.get_model_with_provider(model_key, connection=connection)
            if pair is None:
                raise ValueError("所选模型不存在或当前不可用。")
            row, provider_row = pair
            if row["scope"] != "global" and row["created_by"] != user_id:
                raise ValueError("所选模型不存在或当前不可用。")
            effective = await self._model_api_key(row, provider_row, user_id, connection=connection)
            return resolve_model_row(row, {**provider_row, "api_key": effective})
        elif model_id:
            raise ValueError("所选模型不存在或当前不可用。")
        return await self._resolve_default_model(user_id)

    async def _resolve_default_model(self, user_id: str) -> ResolvedModel:
        """解析当前默认模型：默认行 → 第一条可见有有效 Key 的行；没有则提示配置。"""

        assert self.settings is not None
        storage = self.require_ready()
        rows = await storage.list_visible_model_rows(user_id)
        candidates = []
        for row in rows:
            provider_row = await storage.get_provider_row(row["provider_key"])
            if provider_row is None or (row["scope"] == "global" and not provider_row["enabled"]):
                continue
            effective = await self._model_api_key(row, provider_row, user_id)
            if effective:
                candidates.append((row, {**provider_row, "api_key": effective}))
        candidates.sort(
            key=lambda pair: (
                not pair[0]["is_default"],
                pair[0]["scope"] != "user"
                if pair[0]["is_default"]
                else pair[0]["scope"] != "global",
            )
        )
        default_pair = next((pair for pair in candidates if pair[0]["is_default"]), None)
        selected = default_pair or (candidates[0] if candidates else None)
        if selected is not None:
            return resolve_model_row(*selected)
        raise ValueError("暂无可用模型，请先在技能|连接器 → 模型中配置供应商 Key 并添加模型。")
