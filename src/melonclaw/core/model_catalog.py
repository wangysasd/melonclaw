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

DEFAULT_CONTEXT_WINDOW = 1_000_000
MAX_CONTEXT_WINDOW = 2_147_483_647

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
    context_window: int = DEFAULT_CONTEXT_WINDOW
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
            "context_window": self.context_window,
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

    if row["purpose"] != "chat":
        raise ValueError("聊天目录只接受聊天模型。")
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
        "context_window": int(row["context_window"]),
    }


def resolve_model_row(
    row: dict[str, Any], provider_row: dict[str, Any]
) -> ResolvedModel:
    """把可见且启用的 model_configs 行 + 供应商行解析成运行模型。"""

    if row["purpose"] != "chat":
        raise ValueError("所选模型不是聊天模型。")
    return _resolve_generation_row(row, provider_row)


def _resolve_generation_row(row, provider_row):
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
        context_window=int(row["context_window"]),
        config_version=int(row["version"]),
        provider_version=int(provider_row["version"]),
        request_headers=dict(provider_row["request_headers"]),
        extra_config=dict(provider_row["extra_config"]),
    )


@dataclass(frozen=True)
class ResolvedKnowledgeModel:
    """知识库用途不携带聊天上下文窗口；只接受数据库共享凭据。"""
    id: str
    purpose: str
    model_name: str
    base_url: str
    api_key: str = field(repr=False)
    request_headers: dict[str, str] = field(default_factory=dict, repr=False)
    timeout: float = 30


def resolve_knowledge_model_row(row, provider):
    from melonclaw.core.knowledge import KnowledgeError
    if row["purpose"] != "document_parser" or row["scope"] != "global":
        raise KnowledgeError("仅支持全局知识库用途模型。")
    if not row["enabled"] or not provider["enabled"] or not provider["api_key"]:
        raise KnowledgeError("知识库模型已停用或未配置数据库共享 Key。")
    if provider["provider_type"] != "openai_compatible":
        raise KnowledgeError("知识库用途首版仅支持 OpenAI 兼容供应商。")
    timeout = provider["extra_config"].get("timeout", 30)
    if type(timeout) not in (float, int) or not 0 < timeout <= 120:
        raise KnowledgeError("知识库模型超时必须为 0 到 120 秒之间的数值。")
    return ResolvedKnowledgeModel(str(row["id"]), row["purpose"], row["model_name"],
        str(provider["base_url"]), provider["api_key"], dict(provider["request_headers"]), timeout)
