"""运行配置与模型工厂所需的环境变量。"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from melonclaw.core.mcp_config import (
    load_agent_mcp_servers,
    load_mcp_tool_allowlists,
)


def _create_runtime_dir() -> Path:
    """在项目根目录的 ``temp/`` 下创建运行目录，并在进程退出时清理。"""

    project_temp_dir = Path(__file__).resolve().parents[3] / "temp"
    project_temp_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(
        tempfile.mkdtemp(
            prefix="melonclaw-",
            dir=project_temp_dir,
        )
    )
    atexit.register(shutil.rmtree, runtime_dir, ignore_errors=True)
    return runtime_dir


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
class Settings:
    """一次运行所需的非敏感配置。

    ``api_key`` 只在创建模型时使用，任何展示配置的代码都不要打印它。
    """

    provider: str
    model_name: str
    api_key: str = field(repr=False)
    base_url: str | None
    runtime_dir: Path
    workspace_root: Path
    database_url: str = field(default="", repr=False)
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    mcp_tool_allowlists: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
        repr=False,
    )
    tavily_api_key: str = field(default="", repr=False)

    @property
    def model_spec(self) -> str:
        """返回适合日志展示的 provider:model 标识。"""

        return f"{self.provider}:{self.model_name}"

    @property
    def psycopg_database_url(self) -> str:
        """把业务 asyncpg URL 派生为 Checkpointer 使用的 psycopg URL。"""

        from melonclaw.core.database import derive_psycopg_database_url

        return derive_psycopg_database_url(self.database_url)

    def validate(self) -> None:
        """在发起模型请求前，给出不泄露密钥的配置错误。"""

        if not self.api_key:
            env_name = {
                "deepseek": "DEEPSEEK_API_KEY",
                "openai": "OPENAI_API_KEY",
            }.get(self.provider, f"{self.provider.upper()}_API_KEY")
            raise RuntimeError(f"缺少 {env_name}，请在 .env 中配置模型 API Key。")

        if not self.model_name:
            env_name = {
                "deepseek": "DEEPSEEK_MODEL",
                "openai": "OPENAI_MODEL",
            }.get(self.provider, f"{self.provider.upper()}_MODEL")
            raise RuntimeError(f"缺少 {env_name}，请在 .env 中配置模型名称。")

        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError("模型 base URL 必须是 http(s) URL。")


def load_settings(provider: str | None = None) -> Settings:
    """加载项目 ``.env``，默认使用项目约定的 DeepSeek 配置。"""

    selected_provider = (provider or os.getenv("DEEPAGENTS_PROVIDER", "deepseek")).lower()
    mcp_servers = load_agent_mcp_servers()
    mcp_tool_allowlists = load_mcp_tool_allowlists(mcp_servers)

    if selected_provider == "deepseek":
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        return Settings(
            provider=selected_provider,
            model_name=os.getenv("DEEPSEEK_MODEL", ""),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=base_url,
            runtime_dir=_create_runtime_dir(),
            workspace_root=_create_workspace_root(),
            database_url=os.getenv("DATABASE_URL", ""),
            mcp_servers=mcp_servers,
            mcp_tool_allowlists=mcp_tool_allowlists,
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        )

    if selected_provider == "openai":
        return Settings(
            provider=selected_provider,
            model_name=os.getenv("OPENAI_MODEL", ""),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL"),
            runtime_dir=_create_runtime_dir(),
            workspace_root=_create_workspace_root(),
            database_url=os.getenv("DATABASE_URL", ""),
            mcp_servers=mcp_servers,
            mcp_tool_allowlists=mcp_tool_allowlists,
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        )

    raise ValueError(
        f"暂不支持 provider={selected_provider!r}。可选值：deepseek、openai。"
    )
