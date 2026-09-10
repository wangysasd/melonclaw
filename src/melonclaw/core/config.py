"""运行配置与模型工厂所需的环境变量。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from melonclaw.core.defaults import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEEPSEEK_FLASH_MODEL_ID,
    DEEPSEEK_PRO_MODEL_ID,
    MINIMAX_M27_MODEL_ID,
    MINIMAX_M3_MODEL_ID,
    OPENAI_MODEL_ID,
)
from melonclaw.core.mcp_config import (
    load_agent_mcp_servers,
    load_mcp_tool_allowlists,
)


def _create_workspace_root() -> Path:
    """返回跨进程保留的 Project 工作区根目录。"""

    configured = os.getenv("MELONCLAW_WORKSPACE_DIR", "").strip()
    workspace_root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".melonclaw" / "workspaces"
    )
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root.resolve()


@dataclass(frozen=True)
class ProviderConfig:
    """一个供应商的连接配置和模型槽位。"""

    provider: str
    api_key_env: str
    api_key: str = field(repr=False)
    base_url_env: str
    base_url: str | None
    models: Mapping[str, str] = field(default_factory=dict, repr=False)
    model_env_names: Mapping[str, str] = field(default_factory=dict, repr=False)
    default_model_key: str = ""

    def validate(self, model_key: str, model_name: str | None = None) -> None:
        """校验某个模型槽位，不把密钥内容写入错误信息。"""

        if not self.api_key:
            raise RuntimeError(
                f"缺少 {self.api_key_env}，请在 .env 中配置模型 API Key。"
            )

        selected_model = model_name or self.models.get(model_key, "")
        if not selected_model:
            env_name = self.model_env_names.get(
                model_key,
                f"{self.provider.upper()}_MODEL",
            )
            raise RuntimeError(
                f"缺少 {env_name}，请在 .env 中配置模型名称。"
            )

        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(
                    f"{self.base_url_env} 必须是 http(s) URL。"
                )


@dataclass(frozen=True)
class Settings:
    """一次运行所需的非敏感配置。

    ``api_key`` 只在创建模型时使用，任何展示配置的代码都不要打印它。
    """

    provider: str
    model_name: str
    api_key: str = field(repr=False)
    base_url: str | None
    workspace_root: Path
    database_url: str = field(default="", repr=False)
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    mcp_tool_allowlists: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
        repr=False,
    )
    tavily_api_key: str = field(default="", repr=False)
    provider_configs: Mapping[str, ProviderConfig] = field(
        default_factory=dict,
        repr=False,
    )
    default_model_id: str = ""
    default_model_key: str = ""

    @property
    def model_spec(self) -> str:
        """返回适合日志展示的 provider:model 标识。"""

        return f"{self.provider}:{self.model_name}"

    @property
    def psycopg_database_url(self) -> str:
        """把业务 asyncpg URL 派生为 Checkpointer 使用的 psycopg URL。"""

        from melonclaw.database import derive_psycopg_database_url

        return derive_psycopg_database_url(self.database_url)

    def validate(self) -> None:
        """在发起模型请求前，给出不泄露密钥的配置错误。"""

        provider_config = self.provider_configs.get(self.provider)
        if provider_config is not None:
            provider_config.validate(self.default_model_key, self.model_name)
            return

        # 兼容外部代码直接构造旧版 Settings 的场景。
        if not self.api_key:
            env_name = f"{self.provider.upper()}_API_KEY"
            raise RuntimeError(f"缺少 {env_name}，请在 .env 中配置模型 API Key。")

        if not self.model_name:
            env_name = f"{self.provider.upper()}_MODEL"
            raise RuntimeError(f"缺少 {env_name}，请在 .env 中配置模型名称。")

        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError("模型 base URL 必须是 http(s) URL。")


def _load_provider_configs() -> dict[str, ProviderConfig]:
    """从 .env 读取所有平台供应商，不在此处选择具体模型。"""

    # DEEPSEEK_MODEL 是早期一阶段配置名，仅作为一次升级兼容兜底；新配置统一
    # 使用 DEEPSEEK_MODEL_FLASH / DEEPSEEK_MODEL_PRO。
    deepseek_flash = os.getenv(
        "DEEPSEEK_MODEL_FLASH",
        os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
    )
    return {
        "deepseek": ProviderConfig(
            provider="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url_env="DEEPSEEK_BASE_URL",
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            models={
                "flash": deepseek_flash,
                "pro": os.getenv("DEEPSEEK_MODEL_PRO", ""),
            },
            model_env_names={
                "flash": "DEEPSEEK_MODEL_FLASH",
                "pro": "DEEPSEEK_MODEL_PRO",
            },
            default_model_key="flash",
        ),
        "minimax": ProviderConfig(
            provider="minimax",
            api_key_env="MINIMAX_API_KEY",
            api_key=os.getenv("MINIMAX_API_KEY", ""),
            base_url_env="MINIMAX_BASE_URL",
            base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.cn/v1"),
            models={
                "m3": os.getenv("MINIMAX_MODEL_M3", ""),
                "m27": os.getenv("MINIMAX_MODEL_M27", ""),
            },
            model_env_names={
                "m3": "MINIMAX_MODEL_M3",
                "m27": "MINIMAX_MODEL_M27",
            },
            default_model_key="m3",
        ),
        "openai": ProviderConfig(
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url_env="OPENAI_BASE_URL",
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            models={"default": os.getenv("OPENAI_MODEL", DEFAULT_MODEL)},
            model_env_names={"default": "OPENAI_MODEL"},
            default_model_key="default",
        ),
    }


def _resolve_default_model(
    selector: str,
    provider_configs: Mapping[str, ProviderConfig],
) -> tuple[str, str, ProviderConfig, str]:
    """把 DEEPAGENTS_PROVIDER 解析为供应商、模型 ID 和模型槽位。"""

    selections = {
        "DEEPSEEK_MODEL_FLASH": ("deepseek", DEEPSEEK_FLASH_MODEL_ID, "flash"),
        "DEEPSEEK_MODEL_PRO": ("deepseek", DEEPSEEK_PRO_MODEL_ID, "pro"),
        "MINIMAX_MODEL_M3": ("minimax", MINIMAX_M3_MODEL_ID, "m3"),
        "MINIMAX_MODEL_M27": ("minimax", MINIMAX_M27_MODEL_ID, "m27"),
        # 兼容已经存在的部署配置；新配置统一使用上面的模型键。
        "DEEPSEEK": ("deepseek", DEEPSEEK_FLASH_MODEL_ID, "flash"),
        "DEEPSEEK:FLASH": ("deepseek", DEEPSEEK_FLASH_MODEL_ID, "flash"),
        "DEEPSEEK:PRO": ("deepseek", DEEPSEEK_PRO_MODEL_ID, "pro"),
        "MINIMAX": ("minimax", MINIMAX_M3_MODEL_ID, "m3"),
        "MINIMAX:M3": ("minimax", MINIMAX_M3_MODEL_ID, "m3"),
        "MINIMAX:M27": ("minimax", MINIMAX_M27_MODEL_ID, "m27"),
        "OPENAI": ("openai", OPENAI_MODEL_ID, "default"),
    }
    selection = selections.get(selector.strip().upper())
    if selection is None:
        raise ValueError(
            f"不支持的 DEEPAGENTS_PROVIDER={selector!r}。可选值："
            "DEEPSEEK_MODEL_FLASH、DEEPSEEK_MODEL_PRO、MINIMAX_MODEL_M3、"
            "MINIMAX_MODEL_M27。"
        )
    provider, model_id, model_key = selection
    config = provider_configs[provider]
    return provider, model_id, config, model_key


def load_settings(provider: str | None = None) -> Settings:
    """加载项目 ``.env``，并保留所有供应商的模型配置。"""

    default_selector = (
        provider or os.getenv("DEEPAGENTS_PROVIDER", DEFAULT_PROVIDER)
    ).strip()
    mcp_servers = load_agent_mcp_servers()
    mcp_tool_allowlists = load_mcp_tool_allowlists(mcp_servers)
    provider_configs = _load_provider_configs()
    selected_provider, default_model_id, selected_config, default_model_key = (
        _resolve_default_model(default_selector, provider_configs)
    )

    selected_model_name = selected_config.models.get(
        default_model_key,
        "",
    )
    return Settings(
        provider=selected_provider,
        model_name=selected_model_name,
        api_key=selected_config.api_key,
        base_url=selected_config.base_url,
        workspace_root=_create_workspace_root(),
        database_url=os.getenv("DATABASE_URL", ""),
        mcp_servers=mcp_servers,
        mcp_tool_allowlists=mcp_tool_allowlists,
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        provider_configs=provider_configs,
        default_model_id=default_model_id,
        default_model_key=default_model_key,
    )
