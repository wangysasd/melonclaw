"""环境变量权限、脱敏管理接口与运行资源选择。"""

from melonclaw.core.defaults import managed_env_name
from melonclaw.repository.environment import EnvironmentConflict


class EnvironmentError(ValueError):
    status_code = 422


class EnvironmentPermission(EnvironmentError):
    status_code = 403


class EnvironmentNotFound(EnvironmentError):
    status_code = 404


def environment_scope(user):
    return "global" if user.tenant_role in {"admin", "owner"} else "user"


def validate_fields(name, fields, *, create=False):
    if not managed_env_name(name):
        raise EnvironmentError("变量名无效，或属于系统基础环境、平台凭据或执行控制变量。")
    if set(fields) - {"value", "description", "enabled"}:
        raise EnvironmentError("环境变量字段无效。")
    if create and "value" not in fields:
        raise EnvironmentError("新增变量必须完整提供名称和值。")
    if "value" in fields:
        value = fields["value"]
        if not isinstance(value, str) or "\x00" in value:
            raise EnvironmentError("值必须是最多 8192 字节且不含 NUL 的字符串。")
        try:
            value_size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise EnvironmentError("值必须是有效 UTF-8 文本。") from None
        if value_size > 8192:
            raise EnvironmentError("值必须是最多 8192 字节且不含 NUL 的字符串。")
    if "description" in fields:
        description = fields["description"]
        if (
            not isinstance(description, str)
            or len(description) > 500
            or "\x00" in description
        ):
            raise EnvironmentError("说明最多 500 字符。")
        try:
            description.encode("utf-8")
        except UnicodeEncodeError:
            raise EnvironmentError("说明必须是有效 UTF-8 文本。") from None
    if "enabled" in fields and type(fields["enabled"]) is not bool:
        raise EnvironmentError("启用状态无效。")


def mask_environment_value(value: str) -> str:
    """管理列表只返回脱敏预览，短值不暴露原文。"""
    if len(value) <= 8:
        return "***********"
    return f"{value[:4]}***{value[-4:]}"


class EnvironmentService:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def storage(self):
        return self.runtime.require_ready()

    async def identity(self, user_id):
        user = await self.storage.get_user_context(user_id)
        if user is None:
            raise EnvironmentPermission("用户或所属租户已停用。")
        return user

    async def list(self, user_id):
        user = await self.identity(user_id)
        scope = environment_scope(user)
        rows = await self.storage.list_environment(user_id)
        shared = {row["name"] for row in rows if row["scope"] == "global" and row["enabled"]}
        items = []
        for row in rows:
            if row["scope"] != scope:
                continue
            item = {key: row[key] for key in ("name", "description", "scope", "enabled", "version")}
            item.update(
                id=str(row["id"]),
                masked_value=mask_environment_value(row["value"]),
                updated_at=row["updated_at"].isoformat(),
                effective_source=(
                    "personal" if row["enabled"] else "shared" if row["name"] in shared else "none"
                )
                if scope == "user"
                else ("shared" if row["enabled"] else "none"),
            )
            items.append(item)
        return {"items": items, "scope": scope}

    async def change(
        self,
        user_id,
        *,
        name=None,
        identifier=None,
        expected_version=None,
        fields=None,
        delete=False,
        user=None,
    ):
        user = user or await self.identity(user_id)
        if user.user_id != user_id:
            raise EnvironmentPermission("用户不匹配。")
        scope = environment_scope(user)
        fields = dict(fields or {})
        rows = await self.storage.list_environment(user_id)
        existing = (
            next((row for row in rows if str(row["id"]) == str(identifier)), None)
            if identifier
            else None
        )
        if identifier:
            if existing is None or existing["scope"] != scope:
                raise EnvironmentNotFound("环境变量不存在或无权维护。")
            if expected_version != existing["version"]:
                raise EnvironmentConflict("变量已变化，请刷新后重试。")
            name = existing["name"]
        if not delete:
            validate_fields(name or "", fields, create=not identifier)
        providers = await self.storage.list_provider_rows(include_disabled=True)
        if (
            not delete
            and (not identifier or "value" in fields or fields.get("enabled") is True)
            and any(row["api_key_env"] == name for row in providers)
        ):
            raise EnvironmentError("该名称用于模型共享凭据，不能注入 Skill 环境。")
        if not identifier:
            fields = {"description": "", "enabled": True, **fields}
        return dict(
            name=name,
            scope=scope,
            owner_user_id=None if scope == "global" else user_id,
            created_by=user_id,
            action="delete" if delete else "update" if identifier else "create",
            id=str(identifier) if identifier else None,
            expected_version=expected_version,
            fields=fields,
        )

    async def save(self, user_id, **kwargs):
        change = await self.change(user_id, **kwargs)
        return await self.storage.save_environment(change)
