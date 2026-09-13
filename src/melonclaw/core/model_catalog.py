"""平台内置模型目录与当前运行时模型解析。

第一阶段的模型目录由代码维护，供应商连接信息和每个模型的实际名称
由 ``Settings`` 从环境变量加载。这样前端拿到的是稳定的 ``model_id``，
而不是可以被任意伪造的 provider、Base URL 或模型参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from melonclaw.core.config import ProviderConfig, Settings
from melonclaw.core.defaults import (
    DEEPSEEK_FLASH_MODEL_ID,
    DEEPSEEK_PRO_MODEL_ID,
    MINIMAX_M3_MODEL_ID,
    MINIMAX_M27_MODEL_ID,
    OPENAI_MODEL_ID,
)

MODEL_SOURCE = Literal["system"]

# 这是兼容第一版下拉框的旧 ID。新请求使用具体的供应商/模型槽位 ID。
LEGACY_SYSTEM_DEFAULT_MODEL_ID = "system:default"
# 对外保留这个名字，避免已有调用方把它当作“默认模型 ID”时失效；当前
# 默认槽位由 Settings.default_model_id 决定，通常是 DeepSeek Flash。
SYSTEM_DEFAULT_MODEL_ID = DEEPSEEK_FLASH_MODEL_ID


@dataclass(frozen=True)
class SystemModelDefinition:
    """代码中声明的系统模型条目。"""

    model_id: str
    display_name: str
    provider: str
    model_key: str
    source: MODEL_SOURCE = "system"
    adapter_type: str = "openai_compatible"
    visible_in_catalog: bool = True


# 第一阶段的系统模型目录。后续增加系统模型时，在这里增加代码条目，
# 并在 Settings 中声明对应的环境变量；不通过前端传入任意模型名。
SYSTEM_MODEL_CATALOG: tuple[SystemModelDefinition, ...] = (
    SystemModelDefinition(
        model_id=DEEPSEEK_FLASH_MODEL_ID,
        display_name="DeepSeek Flash",
        provider="deepseek",
        model_key="flash",
    ),
    SystemModelDefinition(
        model_id=DEEPSEEK_PRO_MODEL_ID,
        display_name="DeepSeek Pro",
        provider="deepseek",
        model_key="pro",
    ),
    SystemModelDefinition(
        model_id=MINIMAX_M3_MODEL_ID,
        display_name="MiniMax M3",
        provider="minimax",
        model_key="m3",
    ),
    SystemModelDefinition(
        model_id=MINIMAX_M27_MODEL_ID,
        display_name="MiniMax M2.7",
        provider="minimax",
        model_key="m27",
    ),
    SystemModelDefinition(
        model_id=OPENAI_MODEL_ID,
        display_name="OpenAI",
        provider="openai",
        model_key="default",
        # 保留 OpenAI 兼容适配器给旧部署使用，但第一阶段不放进模型下拉框。
        visible_in_catalog=False,
    ),
)


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
    config_version: int = 1

    @property
    def model_spec(self) -> str:
        return f"{self.provider}:{self.model_name}"

    @property
    def cache_key(self) -> tuple[str, int, str, str, str]:
        """返回不含密钥的 Agent 缓存键，区分历史模型快照。"""

        return (
            self.profile_id,
            self.config_version,
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
        }


def _definition(model_id: str) -> SystemModelDefinition:
    for item in SYSTEM_MODEL_CATALOG:
        if item.model_id == model_id:
            return item
    raise ValueError("所选模型不存在或当前不可用。")


def _provider_config(
    settings: Settings,
    definition: SystemModelDefinition,
) -> ProviderConfig:
    config = settings.provider_configs.get(definition.provider)
    if config is None:
        raise ValueError("所选模型的供应商未配置。")
    return config


def list_system_models(settings: Settings) -> list[dict[str, Any]]:
    """返回当前部署可展示的完整模型目录，不泄露凭据。

    非默认模型可以没有配置，此时它不会阻止服务启动，也不会进入 API
    返回值和前端下拉框。默认模型是否完整由 ``Settings.validate`` 单独校验。
    """

    items: list[dict[str, Any]] = []
    for item in SYSTEM_MODEL_CATALOG:
        if not item.visible_in_catalog:
            continue
        config = settings.provider_configs.get(item.provider)
        model_name = config.models.get(item.model_key, "") if config else ""
        if not config or not config.api_key or not model_name:
            continue
        items.append(
            {
                "id": item.model_id,
                "display_name": item.display_name,
                "source": item.source,
                "provider": item.provider,
                "model": model_name,
                "available": True,
                "is_default": item.model_id == settings.default_model_id,
            }
        )
    return items


def resolve_system_model(
    settings: Settings,
    model_id: str | None = None,
    *,
    model_name: str | None = None,
) -> ResolvedModel:
    """解析请求或历史消息中的系统模型 ID。"""

    selected_id = model_id or settings.default_model_id or SYSTEM_DEFAULT_MODEL_ID
    if selected_id == LEGACY_SYSTEM_DEFAULT_MODEL_ID:
        selected_id = settings.default_model_id or SYSTEM_DEFAULT_MODEL_ID
    item = _definition(selected_id)
    config = _provider_config(settings, item)
    selected_model_name = model_name or config.models.get(item.model_key, "")
    config.validate(item.model_key, selected_model_name)
    if not selected_model_name:
        raise ValueError("系统模型名称未配置。")
    return ResolvedModel(
        profile_id=item.model_id,
        display_name=item.display_name,
        source=item.source,
        adapter_type=item.adapter_type,
        provider=item.provider,
        model_name=selected_model_name,
        base_url=config.base_url,
        api_key=config.api_key,
    )
