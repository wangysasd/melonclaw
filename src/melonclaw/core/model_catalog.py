"""模型目录条目与运行时模型解析。

模型目录以数据库 ``model_providers`` + ``model_configs`` 两张表为唯一事实
来源：供应商持有连接（base_url）与凭据（api_key），模型通过 ``provider_key``
引用供应商。初始化不创建模型。管理员配置全局内置模型，普通用户配置个人模型。
对外模型 ID 统一为 ``custom:{model_key}``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MODEL_SOURCE = Literal["system", "custom"]

CUSTOM_MODEL_ID_PREFIX = "custom:"

@dataclass(frozen=True)
class ResolvedModel:
    """一次 Agent 运行实际绑定的模型配置。"""

    profile_id: str
    display_name: str
    source: MODEL_SOURCE
    adapter_type: str
    provider: str
    model_name: str
    base_url: str | None
    api_key: str = field(repr=False)
    input_modalities: frozenset[str] = frozenset({"text"})
    config_version: int = 1
    provider_version: int = 1
    request_headers: dict[str, str] = field(default_factory=dict, repr=False)
    extra_config: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def model_spec(self) -> str:
        return f"{self.provider}:{self.model_name}"

    @property
    def cache_key(self) -> tuple[str, int, int, str, str, str]:
        """返回不含密钥的 Agent 缓存键，区分历史模型快照。

        同时携带模型版本与供应商版本：供应商换 Key/改地址后旧缓存
        即时失效，无需等待模型行自身变更。
        """

        return (
            self.profile_id,
            self.config_version,
            self.provider_version,
            self.provider,
            self.model_name,
            self.base_url or "",
        )

    def public_dict(self) -> dict[str, Any]:
        """返回可以放入 API/SSE 的非敏感模型信息。"""

        return {
            "id": self.profile_id,
            "display_name": self.display_name,
            "source": self.source,
            "provider": self.provider,
            "model": self.model_name,
            "config_version": self.config_version,
            "provider_version": self.provider_version,
            "input_modalities": sorted(self.input_modalities),
        }


def custom_model_id(model_key: str) -> str:
    return f"{CUSTOM_MODEL_ID_PREFIX}{model_key}"


def is_custom_model_id(model_id: str) -> bool:
    return model_id.startswith(CUSTOM_MODEL_ID_PREFIX)


def _row_modalities(row: dict[str, Any]) -> list[str]:
    return [str(item) for item in row["input_modalities"]]


def catalog_item(
    row: dict[str, Any], provider_row: dict[str, Any]
) -> dict[str, Any]:
    """把 model_configs 行 + 供应商行转成模型目录条目，绝不携带 api_key。"""

    source: MODEL_SOURCE = (
        "system" if row["scope"] == "global" else "custom"
    )
    return {
        "id": custom_model_id(str(row["model_key"])),
        "display_name": row["display_name"],
        "source": source,
        "provider": provider_row["display_name"],
        "provider_key": str(provider_row["provider_key"]),
        "model": row["model_name"],
        # 全局模型受供应商开关控制；个人模型只依赖自身开关与个人 Key。
        # api_key 由运行时按模型 scope 解析，目录与实际调用保持一致。
        "available": bool(row["enabled"])
        and (row["scope"] == "user" or bool(provider_row["enabled"]))
        and bool(provider_row["api_key"]),
        "is_default": bool(row["is_default"]),
        "scope": row["scope"],
        "input_modalities": _row_modalities(row),
    }


def resolve_model_row(
    row: dict[str, Any], provider_row: dict[str, Any]
) -> ResolvedModel:
    """把可见且启用的 model_configs 行 + 供应商行解析成运行模型。"""

    if not row["enabled"]:
        raise ValueError("所选模型已被停用。")
    if row["scope"] == "global" and not provider_row["enabled"]:
        raise ValueError("所选模型所属供应商已被停用。")
    api_key = provider_row["api_key"]
    if not api_key:
        raise ValueError("所选模型所属供应商未配置 API Key。")
    source: MODEL_SOURCE = (
        "system" if row["scope"] == "global" else "custom"
    )
    return ResolvedModel(
        profile_id=custom_model_id(str(row["model_key"])),
        display_name=row["display_name"],
        source=source,
        adapter_type=str(provider_row["provider_type"]),
        provider=str(provider_row["provider_key"]),
        model_name=row["model_name"],
        base_url=provider_row["base_url"],
        api_key=api_key,
        input_modalities=frozenset(_row_modalities(row)),
        config_version=int(row["version"]),
        provider_version=int(provider_row["version"]),
        request_headers=dict(provider_row["request_headers"]),
        extra_config=dict(provider_row["extra_config"]),
    )
