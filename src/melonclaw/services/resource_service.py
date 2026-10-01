"""Skill/MCP/自定义模型资源管理：权限校验 + 数据库与文件系统的组合操作。

权限矩阵（tenant_role 判定，无新角色体系）：
- Skill/MCP/模型保持两级 scope：任何用户可管理自己 user scope，
  admin/owner 额外可管理 global；
- 供应商仅管理员可管理，且只能创建 global scope；共享模型全员可用。
  普通用户在既有供应商上配置自己的 Key 和私有模型；
- 普通用户可在共享供应商上覆盖自己的 API Key（`provider_user_keys`），
  调用时用户 Key 优先于共享 Key；
- 任何人（包括 admin）不能修改他人创建的 user scope 资源；
- user scope 的 MCP 仅允许 http/sse transport，配置值禁止 ``${VAR}`` 占位符。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from melonclaw.core.config import provider_env_key
from melonclaw.services.provider_config import ModelConfigError, validate_provider_advanced
from melonclaw.services.skill_content import check_requirements, preview_content
from melonclaw.services.skill_import import MAX_ARCHIVE_ENTRIES, MAX_ARCHIVE_TOTAL_BYTES
from melonclaw.services.skill_index import reindex_skills_from_disk
from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import SAFE_DIRECTORY_RE

ADMIN_ROLES = frozenset({"admin", "owner"})
MCP_TRANSPORTS = ("http", "sse", "stdio")

MODEL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")



class SkillStateError(ValueError):
    """Skill 状态变更违反约定（如对私有 Skill 做全员启停）。"""

    status_code = 422


class ResourcePermissionError(PermissionError):
    """当前用户无权对该资源执行此操作。"""

    status_code = 403


class ResourceNotFoundError(LookupError):
    """资源不存在或当前用户不可见。"""

    status_code = 404


@dataclass(frozen=True)
class ProviderConfigPayload:
    """创建模型供应商的请求载荷。"""

    provider_key: str
    scope: str
    display_name: str
    base_url: str
    provider_type: str = "openai_compatible"
    api_key_env: str = ""
    request_headers: dict[str, str] = field(default_factory=dict, repr=False)
    extra_config: dict[str, Any] = field(default_factory=dict)
    api_key: str | None = None
    models_endpoint: str | None = None
    enabled: bool = True


def validate_provider_payload(payload: ProviderConfigPayload) -> None:
    """校验供应商配置；密钥可为空（表示创建后待补 Key，运行时拒绝调用）。"""

    validate_provider_advanced(payload.api_key_env, payload.request_headers, payload.extra_config)
    if payload.provider_type != "openai_compatible":
        raise ModelConfigError("当前仅支持 OpenAI Completions API。")
    if not MODEL_KEY_RE.fullmatch(payload.provider_key):
        raise ModelConfigError(
            "供应商标识只能包含小写字母、数字、下划线和连字符。"
        )
    if not payload.display_name.strip():
        raise ModelConfigError("供应商显示名称不能为空。")
    base_url = payload.base_url.strip()
    if not base_url.startswith(("https://", "http://")):
        raise ModelConfigError("Base URL 必须以 http(s):// 开头。")
    if payload.models_endpoint is not None:
        endpoint = payload.models_endpoint.strip()
        if endpoint and not endpoint.startswith(("https://", "http://")):
            raise ModelConfigError("模型列表端点必须以 http(s):// 开头。")


def provider_public_dict(
    row: dict[str, Any],
    *,
    enabled_models_count: int = 0,
    has_my_key: bool = False,
) -> dict[str, Any]:
    """供应商行的安全展示：api_key 绝不回显，只标记是否已配置。

    - ``has_api_key``：admin 配的共享 Key 有无；
    - ``has_my_key``：当前用户覆盖的 Key 有无；
    - ``effective_has_key``：两者任一有即 True，前端据此判断可用性；
    - ``enabled_models_count``：该供应商下已启用模型数（Yuxi 卡片 footer 用）。
    """

    has_shared = bool(row["api_key"] or provider_env_key(row["api_key_env"]))
    return {
        "provider_key": row["provider_key"],
        "scope": row["scope"],
        "source_type": row["source_type"],
        "display_name": row["display_name"],
        "provider_type": row["provider_type"],
        "base_url": row["base_url"],
        "models_endpoint": row["models_endpoint"],
        "api_key_env": row["api_key_env"],
        "has_request_headers": bool(row["request_headers"]),
        "extra_config": row["extra_config"],
        "has_api_key": has_shared,
        "has_my_key": bool(has_my_key),
        "effective_has_key": bool(has_shared or has_my_key),
        "enabled_models_count": int(enabled_models_count),
        "enabled": bool(row["enabled"]),
        "created_by": row["created_by"],
    }


@dataclass(frozen=True)
class ModelConfigPayload:
    """创建自定义模型的请求载荷。"""

    model_key: str
    provider_key: str
    scope: str
    display_name: str
    model_name: str
    enabled: bool = True


def validate_model_payload(payload: ModelConfigPayload) -> None:
    """校验模型配置；连接与凭据归属供应商，模型只记名称。"""

    if not MODEL_KEY_RE.fullmatch(payload.model_key):
        raise ModelConfigError(
            "模型标识只能包含小写字母、数字、下划线和连字符。"
        )
    if not payload.provider_key.strip():
        raise ModelConfigError("供应商标识不能为空。")
    if not payload.display_name.strip():
        raise ModelConfigError("模型显示名称不能为空。")
    if not payload.model_name.strip():
        raise ModelConfigError("模型名称不能为空。")


def model_public_dict(row: dict[str, Any], provider_row: dict[str, Any]) -> dict[str, Any]:
    """模型行的安全展示：供应商信息联查展示，绝不携带 api_key。"""

    return {
        "model_key": row["model_key"],
        "provider_key": row["provider_key"],
        "provider_display_name": provider_row["display_name"],
        "scope": row["scope"],
        "source_type": row["source_type"],
        "display_name": row["display_name"],
        "model_name": row["model_name"],
        "enabled": bool(row["enabled"]),
        "is_default": bool(row["is_default"]),
        "input_modalities": [str(item) for item in row["input_modalities"]],
        "created_by": row["created_by"],
    }


class ResourceService:
    """组合仓储查询、权限校验和 data_root 文件操作。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def storage(self):
        return self.runtime.require_ready()

    @property
    def data_root(self) -> Path:
        assert self.runtime.settings is not None
        return self.runtime.settings.data_root

    async def _role(self, user_id: str) -> str:
        context = await self.storage.get_user_context(user_id)
        if context is None:
            raise ResourcePermissionError("用户不存在或已停用。")
        return context.tenant_role

    def _require_admin(self, role: str) -> None:
        if role not in ADMIN_ROLES:
            raise ResourcePermissionError("该操作需要管理员权限。")

    def _skill_dir(self, row: dict[str, Any]) -> Path:
        return self.data_root / "skills" / str(row["storage_path"])

    def _checked_skill_dir(self, row: dict[str, Any]) -> Path:
        """Resolve a DB storage path without allowing traversal or symlink escape."""

        name = str(row["name"])
        relative_path = Path(str(row["storage_path"]))
        skills_root = self.data_root / "skills"
        if (
            not SAFE_DIRECTORY_RE.fullmatch(name)
            or relative_path.is_absolute()
            or not relative_path.parts
            or any(part in {".", ".."} for part in relative_path.parts)
        ):
            raise SkillStateError("技能存储路径无效。")

        directory = skills_root / relative_path
        current = skills_root
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise SkillStateError("技能存储路径包含不支持的符号链接。")
        try:
            resolved_root = skills_root.resolve()
            resolved_directory = directory.resolve(strict=True)
        except OSError:
            raise ResourceNotFoundError("技能目录不存在或无法读取。") from None
        if (
            resolved_directory == resolved_root
            or not resolved_directory.is_relative_to(resolved_root)
            or resolved_directory.name != name
            or not resolved_directory.is_dir()
        ):
            raise SkillStateError("技能存储路径无效。")
        return directory

    async def _resolve_skill_row(
        self, user_id: str, name: str, scope: str
    ) -> dict[str, Any]:
        """把 (name, scope) 解析成唯一一行。

        scope 必填：global 取共享行，user 只取调用者的私有行。
        """

        rows = await self.storage.list_skill_rows_by_name(name)
        if scope == "global":
            candidates = [row for row in rows if row["scope"] == "global"]
        elif scope == "user":
            # 不同用户的私有 Skill 允许同名，优先解析自己的；只有他人的
            # 私有行时报权限错（与“私有仅创建者可管理”一致）。
            own = [
                row
                for row in rows
                if row["scope"] == "user" and row["created_by"] == user_id
            ]
            if own:
                candidates = own
            elif any(row["scope"] == "user" for row in rows):
                raise ResourcePermissionError("不能修改他人创建的私有技能。")
            else:
                candidates = []
        else:
            raise SkillStateError(f"不支持的 scope：{scope!r}。")
        if not candidates:
            raise ResourceNotFoundError(f"技能 {name!r} 不存在。")
        return candidates[0]

    async def download_skill_archive(self, user_id: str, name: str, scope: str) -> bytes:
        async with SkillOperations(self.data_root).locked():
            return await self._download_skill_archive(user_id, name, scope)

    async def _download_skill_archive(
        self, user_id: str, name: str, scope: str
    ) -> bytes:
        """Build a bounded ZIP for a Skill visible to the current user."""

        role = await self._role(user_id)
        row = await self._resolve_skill_row(user_id, name, scope)
        if row["scope"] == "global" and not row["enabled"] and role not in ADMIN_ROLES:
            raise ResourceNotFoundError("技能不可用。")
        if row["scope"] != "global" and row["created_by"] != user_id:
            raise ResourcePermissionError("不能下载他人创建的私有技能。")
        directory = self._checked_skill_dir(row)

        archive_buffer = io.BytesIO()
        entry_count = 0
        total_bytes = 0
        try:
            with zipfile.ZipFile(
                archive_buffer, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for candidate in directory.rglob("*"):
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    relative_path = candidate.relative_to(directory)
                    if any(
                        (directory / Path(*relative_path.parts[:index])).is_symlink()
                        for index in range(1, len(relative_path.parts))
                    ):
                        continue
                    if any("\\" in part for part in relative_path.parts):
                        raise SkillStateError("技能文件名包含不支持的字符，无法打包。")
                    entry_count += 1
                    if entry_count > MAX_ARCHIVE_ENTRIES:
                        raise SkillStateError("技能文件数量超过下载上限。")
                    try:
                        file_size = candidate.stat().st_size
                    except OSError:
                        raise ResourceNotFoundError("技能文件无法读取，无法下载。") from None
                    if total_bytes + file_size > MAX_ARCHIVE_TOTAL_BYTES:
                        raise SkillStateError("技能总大小超过 50 MB，无法打包下载。")
                    try:
                        content = candidate.read_bytes()
                    except OSError:
                        raise ResourceNotFoundError("技能文件无法读取，无法下载。") from None
                    total_bytes += len(content)
                    if total_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                        raise SkillStateError("技能总大小超过 50 MB，无法打包下载。")
                    archive.writestr(
                        f"{name}/{relative_path.as_posix()}", content
                    )
        except OSError:
            raise ResourceNotFoundError("技能文件无法读取，无法下载。") from None
        return archive_buffer.getvalue()

    # ---- Skill 管理 ----

    async def manageable_skills(self, user_id: str) -> dict[str, Any]:
        role = await self._role(user_id)
        rows = await self.storage.list_visible_skill_rows(user_id, include_disabled=True)
        rows = [row for row in rows if row["scope"] != "global" or row["enabled"] or role in {"admin", "owner"}]
        states = evaluate_skills(rows, self.runtime._catalog_for(user_id), self.runtime.settings.data_root / "skills")
        return {"items": [state.public_dict() for state in states]}

    async def set_skill_enabled(
        self, user_id: str, name: str, enabled: bool, scope: str
    ) -> None:
        """个人启停：共享 Skill 写个人偏好（只影响自己），私有 Skill 由创建者改行。

        共享 Skill 对所有用户默认启用；任何用户都可以选择“我自己不用”，
        落到 ``skill_user_states``，不影响其他用户，也不需要管理员权限。
        私有 Skill 与共享 Skill 同名共存时调用方必须传 scope 消歧。
        """

        await self._role(user_id)
        row = await self._resolve_skill_row(user_id, name, scope)
        if row["scope"] == "global":
            await self.storage.set_skill_user_state(user_id, row["id"], enabled)
            return
        await self.storage.update_skill_row(row["id"], enabled=enabled)

    async def set_skill_global_enabled(
        self, user_id: str, name: str, enabled: bool
    ) -> None:
        """全员启停共享 Skill（写 ``skills.enabled``）；仅 admin/owner。

        停用后该 Skill 对所有用户不可见，个人偏好随之失效（重新启用后，
        各用户原有的个人偏好仍然生效）。
        """

        role = await self._role(user_id)
        self._require_admin(role)
        row = await self._resolve_skill_row(user_id, name, "global")
        await self.storage.update_skill_row(row["id"], enabled=enabled)

    async def delete_skill(
        self, user_id: str, name: str, scope: str
    ) -> None:
        role = await self._role(user_id)
        row = await self._resolve_skill_row(user_id, name, scope)
        if row["scope"] == "global":
            self._require_admin(role)
        operations = SkillOperations(self.runtime.settings.data_root)
        async with operations.locked():
            await operations.recover(self.storage)
            row = await self._resolve_skill_row(user_id, name, scope)
            await operations.delete(self.storage, row)

    async def skill_details(self, user_id: str, name: str, scope: str) -> dict[str, Any]:
        role = await self._role(user_id)
        row = await self._resolve_skill_row(user_id, name, scope)
        if row["scope"] == "global" and not row["enabled"] and role not in {"admin", "owner"}:
            raise ResourceNotFoundError("技能不可用。")
        operations = SkillOperations(self.runtime.settings.data_root)
        async with operations.locked():
            preview = preview_content(self._checked_skill_dir(row))
            preview["dependency_checks"] = await check_requirements(preview["requirements"], self.storage, user_id)
        return preview

    async def recover_skills(self, user_id: str) -> dict[str, Any]:
        self._require_admin(await self._role(user_id))
        report = await reindex_skills_from_disk(self.storage, self.runtime.settings.data_root)
        return {"summary": report.summary(), "missing": list(report.missing),
                "orphaned": list(report.orphaned), "registered": list(report.registered), "invalid": list(report.invalid)}

    # ---- 模型供应商管理（仅管理员配置全局供应商） ----

    async def list_providers(self, user_id: str) -> dict[str, Any]:
        await self._role(user_id)
        rows = await self.storage.list_provider_rows(include_disabled=True)
        my_keys = await self.storage.list_user_provider_keys(user_id)
        items = []
        for row in rows:
            models = await self.storage.list_models_of_provider(
                str(row["provider_key"])
            )
            # 计数只含请求者可见模型（全局 + 自己的），与前端卡片口径一致。
            enabled_count = sum(
                1
                for item in models
                if item.get("enabled")
                and (
                    item.get("scope") == "global"
                    or item.get("created_by") == user_id
                )
            )
            items.append(
                provider_public_dict(
                    row,
                    enabled_models_count=enabled_count,
                    has_my_key=str(row["provider_key"]) in my_keys,
                )
            )
        # 已启用优先，其次凭证缺失沉底（学 Yuxi 排序）。
        items.sort(
            key=lambda item: (
                not item["enabled"],
                not item["effective_has_key"],
                str(item["provider_key"]),
            )
        )
        return {"items": items}

    async def create_provider(
        self, user_id: str, payload: ProviderConfigPayload
    ) -> None:
        role = await self._role(user_id)
        self._require_admin(role)
        if payload.scope != "global":
            raise ModelConfigError("供应商只能创建为全局共享。")
        validate_provider_payload(payload)
        if await self.storage.get_provider_row(payload.provider_key) is not None:
            raise ModelConfigError(f"供应商 {payload.provider_key!r} 已存在。")
        await self.storage.create_provider_row(
            provider_key=payload.provider_key,
            scope=payload.scope,
            source_type="manual",
            display_name=payload.display_name,
            provider_type=payload.provider_type,
            api_key_env=payload.api_key_env,
            request_headers=payload.request_headers,
            extra_config=payload.extra_config,
            base_url=payload.base_url.strip(),
            api_key=payload.api_key,
            models_endpoint=(payload.models_endpoint or "").strip() or None,
            created_by=user_id,
            enabled=payload.enabled,
        )

    async def _require_provider_row(
        self, user_id: str, provider_key: str, role: str
    ) -> dict[str, Any]:
        self._require_admin(role)
        row = await self.storage.get_provider_row(provider_key)
        if row is None:
            raise ResourceNotFoundError(f"供应商 {provider_key!r} 不存在。")
        return row

    async def update_provider(
        self,
        user_id: str,
        provider_key: str,
        *,
        enabled: bool | None = None,
        display_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        models_endpoint: str | None = None,
        api_key_env: str | None = None,
        request_headers: dict[str, str] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        """更新供应商配置；``api_key=None`` 表示不改动已存密钥。"""

        role = await self._role(user_id)
        await self._require_provider_row(user_id, provider_key, role)
        fields: dict[str, Any] = {}
        validate_provider_advanced(api_key_env, request_headers, extra_config)
        for key, value in (("api_key_env", api_key_env), ("request_headers", request_headers), ("extra_config", extra_config)):
            if value is not None:
                fields[key] = value
        if enabled is not None:
            fields["enabled"] = enabled
        if display_name is not None:
            if not display_name.strip():
                raise ModelConfigError("供应商显示名称不能为空。")
            fields["display_name"] = display_name.strip()
        if base_url is not None:
            if not base_url.strip().startswith(("https://", "http://")):
                raise ModelConfigError("Base URL 必须以 http(s):// 开头。")
            fields["base_url"] = base_url.strip()
        if api_key is not None:
            fields["api_key"] = api_key
        if models_endpoint is not None:
            endpoint = models_endpoint.strip()
            if endpoint and not endpoint.startswith(("https://", "http://")):
                raise ModelConfigError("模型列表端点必须以 http(s):// 开头。")
            fields["models_endpoint"] = endpoint or None
        if enabled is False:
            # 停用共享配置时撤销共享 Key 和环境变量引用；个人 Key 独立保留。
            fields["api_key"] = None
            fields["api_key_env"] = ""
        if fields:
            await self.storage.update_provider_row(provider_key, **fields)

    async def delete_provider(self, user_id: str, provider_key: str) -> None:
        role = await self._role(user_id)
        row = await self._require_provider_row(user_id, provider_key, role)
        if row["source_type"] == "system":
            raise ModelConfigError("平台内置供应商不能删除，只能停用或修改。")
        models = await self.storage.list_models_of_provider(provider_key)
        if models:
            raise ModelConfigError(
                f"供应商下还有 {len(models)} 个模型，请先删除这些模型。"
            )
        await self.storage.delete_provider_row(provider_key)

    async def set_user_provider_key(
        self, user_id: str, provider_key: str, api_key: str
    ) -> None:
        """普通用户在共享供应商上覆盖自己的 Key；admin 的共享 Key 不受影响。"""

        await self._role(user_id)
        provider = await self.storage.get_provider_row(provider_key)
        if provider is None:
            raise ResourceNotFoundError(f"供应商 {provider_key!r} 不存在。")
        key = (api_key or "").strip()
        if not key:
            raise ModelConfigError("API Key 不能为空，清除请调用删除接口。")
        await self.storage.upsert_user_provider_key(
            provider_key=provider_key, user_id=user_id, api_key=key
        )

    async def delete_user_provider_key(
        self, user_id: str, provider_key: str
    ) -> None:
        await self._role(user_id)
        await self.storage.delete_user_provider_key(provider_key, user_id)

    async def fetch_remote_models(
        self, user_id: str, provider_key: str
    ) -> dict[str, Any]:
        """按供应商配置实时拉取远端模型列表，不落库。

        管理员使用有效 Key 拉取；普通用户必须使用个人 Key，
        不受供应商全局启停影响。写操作仍在模型管理接口。
        """

        role = await self._role(user_id)
        provider = await self.storage.get_provider_row(provider_key)
        if provider is None:
            raise ResourceNotFoundError(f"供应商 {provider_key!r} 不存在。")
        endpoint = provider["models_endpoint"]
        if not endpoint:
            raise ModelConfigError("该供应商未配置模型列表端点。")
        if role in {"admin", "owner"}:
            api_key = await self.storage.effective_provider_api_key(provider, user_id)
        else:
            personal = await self.storage.get_user_provider_key(provider_key, user_id)
            if not personal:
                raise ModelConfigError("请先配置个人 API Key，再获取远程模型。")
            api_key = personal["api_key"]
        headers = dict(provider["request_headers"])
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(endpoint, headers=headers)
        except httpx.HTTPError as exc:
            raise ModelConfigError("拉取远端模型失败，请检查网络和端点配置。") from exc
        if response.status_code == 401:
            raise ModelConfigError("远端 API 认证失败，请检查供应商 API Key。")
        if response.status_code != 200:
            raise ModelConfigError(
                f"远端模型列表请求失败（HTTP {response.status_code}）。"
            )
        payload = response.json()
        raw_models = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            raise ModelConfigError("远端模型列表响应格式不正确。")
        models = []
        for raw in raw_models:
            if not isinstance(raw, dict):
                continue
            model_id = str(raw.get("id") or "").strip()
            if model_id:
                models.append(
                    {
                        "id": model_id,
                        "display_name": str(
                            raw.get("display_name") or raw.get("name") or model_id
                        ),
                    }
                )
        return {"items": models}

    # ---- 自定义模型管理（两级 scope：admin 配全局共享，用户配私有） ----

    async def list_models(self, user_id: str) -> dict[str, Any]:
        await self._role(user_id)
        rows = await self.storage.list_visible_model_rows(
            user_id, include_disabled=True
        )
        items = []
        for row in rows:
            provider_row = await self.storage.get_provider_row(row["provider_key"])
            if provider_row is None:
                continue
            items.append(model_public_dict(row, provider_row))
        return {"items": items}

    async def create_model(self, user_id: str, payload: ModelConfigPayload) -> None:
        role = await self._role(user_id)
        if payload.scope == "global":
            self._require_admin(role)
        elif payload.scope != "user":
            raise ModelConfigError(f"不支持的 scope：{payload.scope!r}。")
        validate_model_payload(payload)
        provider_row = await self.storage.get_provider_row(payload.provider_key)
        if provider_row is None:
            raise ModelConfigError(f"供应商 {payload.provider_key!r} 不存在。")
        if payload.scope == "global" and not provider_row["enabled"]:
            raise ModelConfigError("所选供应商已停用，不能新建内置模型。")
        if payload.scope == "user":
            personal = await self.storage.get_user_provider_key(
                payload.provider_key, user_id
            )
            if not personal:
                raise ModelConfigError("请先配置个人 API Key，再添加自定义模型。")
        if await self.storage.get_model_row(payload.model_key) is not None:
            raise ModelConfigError(f"模型 {payload.model_key!r} 已存在。")
        await self.storage.create_model_row(
            model_key=payload.model_key,
            provider_key=payload.provider_key,
            scope=payload.scope,
            source_type="manual",
            display_name=payload.display_name,
            model_name=payload.model_name,
            created_by=user_id,
            enabled=payload.enabled,
        )

    async def _require_model_row(
        self, user_id: str, model_key: str, role: str
    ) -> dict[str, Any]:
        row = await self.storage.get_model_row(model_key)
        if row is None:
            raise ResourceNotFoundError(f"模型 {model_key!r} 不存在。")
        if row["scope"] == "global":
            self._require_admin(role)
        elif row["created_by"] != user_id:
            raise ResourcePermissionError("不能修改他人创建的私有模型。")
        return row

    async def update_model(
        self,
        user_id: str,
        model_key: str,
        *,
        enabled: bool | None = None,
        display_name: str | None = None,
        model_name: str | None = None,
        is_default: bool | None = None,
    ) -> None:
        """更新模型配置；连接与凭据的变更走供应商管理。

        默认模型按全局或个人归属切换，权限沿用模型编辑权限。
        """

        role = await self._role(user_id)
        await self._require_model_row(user_id, model_key, role)
        if is_default is not None:
            if not is_default:
                raise ModelConfigError("不支持取消默认模型，请把默认切换给其他模型。")
            await self.storage.set_default_model(model_key)
        fields: dict[str, Any] = {}
        if enabled is not None:
            fields["enabled"] = enabled
        if display_name is not None:
            if not display_name.strip():
                raise ModelConfigError("模型显示名称不能为空。")
            fields["display_name"] = display_name.strip()
        if model_name is not None:
            if not model_name.strip():
                raise ModelConfigError("模型名称不能为空。")
            fields["model_name"] = model_name.strip()
        if fields:
            await self.storage.update_model_row(model_key, **fields)

    async def delete_model(self, user_id: str, model_key: str) -> None:
        role = await self._role(user_id)
        await self._require_model_row(user_id, model_key, role)
        await self.storage.delete_model_row(model_key)


__all__ = [
    "ADMIN_ROLES",
    "MCP_TRANSPORTS",
    "ModelConfigError",
    "ModelConfigPayload",
    "model_public_dict",
    "provider_public_dict",
    "ProviderConfigPayload",
    "ResourceNotFoundError",
    "ResourcePermissionError",
    "ResourceService",
    "SkillStateError",
    "validate_provider_payload",
]
