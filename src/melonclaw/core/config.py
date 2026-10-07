"""应用运行配置；模型与凭据由数据库配置提供。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _validate_persistent_root(root: Path) -> Path:
    resolved = root.resolve()
    project_root = Path(__file__).resolve().parents[3]
    if resolved == project_root or project_root in resolved.parents:
        raise ValueError("持久数据目录必须位于项目代码目录之外，请修改目录环境变量。")
    return resolved


def _create_workspace_root() -> Path:
    """返回跨进程保留的工作区根目录。"""

    configured = os.getenv("MELONCLAW_WORKSPACE_DIR", "").strip()
    workspace_root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".melonclaw" / "workspaces"
    )
    workspace_root = _validate_persistent_root(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root.resolve()


def _create_data_root() -> Path:
    """返回 Skill/MCP 等平台资源数据根目录，与 Agent 工作区隔离。

    这里的文件可能被 Agent 通过虚拟路径只读挂载，但绝不出现在 Shell 工作目录里；
    用户上传内容落盘前必须经过服务层校验。
    """

    configured = os.getenv("MELONCLAW_DATA_DIR", "").strip()
    data_root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".melonclaw" / "data"
    )
    data_root = _validate_persistent_root(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root.resolve()


def _int_setting(name: str, default: int, *, minimum: int = 1) -> int:
    """解析附件资源限制，避免解析器直接读取环境变量。"""

    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数。") from exc
    if value < minimum:
        raise RuntimeError(f"{name} 必须不小于 {minimum}。")
    return value


def _bool_setting(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise RuntimeError(f"{name} 必须是 true/false 或 1/0。")
    return raw in {"true", "1"}


def _ratio_setting(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是 0 到 1 之间的小数。") from exc
    if not 0 < value < 1:
        raise RuntimeError(f"{name} 必须大于 0 且小于 1。")
    return value


@dataclass(frozen=True)
class Settings:
    """应用运行配置，不包含模型连接或模型凭据。"""

    workspace_root: Path
    profile: str = ""
    data_root: Path = field(default_factory=_create_data_root)
    database_url: str = field(default="", repr=False)
    tavily_api_key: str = field(default="", repr=False)
    attachment_max_file_bytes: int = 20 * 1024 * 1024
    attachment_max_per_message: int = 10
    attachment_max_total_bytes: int = 50 * 1024 * 1024
    attachment_project_max_bytes: int = 1024 * 1024 * 1024
    attachment_image_max_pixels: int = 30_000_000
    attachment_pdf_max_pages: int = 500
    attachment_parse_max_chars: int = 2_000_000
    attachment_archive_max_entries: int = 5_000
    attachment_archive_max_uncompressed_bytes: int = 200 * 1024 * 1024
    attachment_archive_max_entry_bytes: int = 50 * 1024 * 1024
    attachment_archive_max_compression_ratio: int = 50
    attachment_staged_ttl_hours: int = 24
    attachment_parse_concurrency: int = 2
    attachment_validation_timeout_seconds: int = 30
    attachment_parse_timeout_seconds: int = 300
    attachment_parse_lease_seconds: int = 60
    attachment_parse_max_attempts: int = 3
    attachment_image_outbound_max_edge: int = 1568
    attachment_image_outbound_jpeg_quality: int = 85
    attachment_image_cache_entries: int = 32
    tool_selection_timeout_seconds: int = 10
    tool_pool_size: int = 16
    tool_selection_size: int = 8
    tool_selection_max_requests: int = 4
    agent_output_reserve: int = 4096
    agent_summary_trigger_ratio: float = 0.8
    agent_summary_keep_tokens: int = 4096
    agent_model_call_limit: int = 30
    agent_tool_call_limit: int = 100
    agent_retry_max_retries: int = 2
    agent_retry_initial_delay: int = 1
    agent_retry_max_delay: int = 10
    agent_usage_enabled: bool = True
    agent_todo_enabled: bool = True
    agent_cache_entries: int = 32
    user_input_ttl_seconds: int = 24 * 60 * 60

    def __post_init__(self) -> None:
        if not 0 < self.agent_summary_trigger_ratio < 1:
            raise ValueError("摘要触发比例必须大于 0 且小于 1。")
        if self.agent_output_reserve <= 0 or self.agent_summary_keep_tokens <= 0:
            raise ValueError("输出预留和摘要保留 token 必须大于 0。")
        if self.agent_retry_max_delay < self.agent_retry_initial_delay:
            raise ValueError("最大重试间隔不能小于初始间隔。")

    def summary_trigger_tokens(self, context_window: int) -> int:
        """按数据库模型窗口计算阈值，并校验输出与近期保留预算。"""
        trigger = int(context_window * self.agent_summary_trigger_ratio)
        if context_window <= self.agent_output_reserve or trigger + self.agent_output_reserve > context_window:
            raise ValueError("模型上下文窗口不足以容纳压缩阈值与输出预留，请调整模型窗口或摘要比例。")
        if self.agent_summary_keep_tokens >= trigger:
            raise ValueError("摘要保留 token 必须小于当前模型的触发阈值。")
        return trigger

    @property
    def psycopg_database_url(self) -> str:
        """把业务 asyncpg URL 派生为 Checkpointer 使用的 psycopg URL。"""

        from melonclaw.database import derive_psycopg_database_url

        return derive_psycopg_database_url(self.database_url)

def load_settings() -> Settings:
    """读取应用环境配置。"""

    return Settings(
        workspace_root=_create_workspace_root(),
        profile=os.getenv("profile", ""),
        database_url=os.getenv("DATABASE_URL", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        tool_selection_timeout_seconds=_int_setting("MELONCLAW_TOOL_SELECTION_TIMEOUT_SECONDS", 10),
        tool_pool_size=_int_setting("MELONCLAW_TOOL_POOL_SIZE", 16),
        tool_selection_size=_int_setting("MELONCLAW_TOOL_SELECTION_SIZE", 8),
        tool_selection_max_requests=_int_setting("MELONCLAW_TOOL_SELECTION_MAX_REQUESTS", 4),
        agent_output_reserve=_int_setting("MELONCLAW_AGENT_OUTPUT_RESERVE", 4096),
        agent_summary_trigger_ratio=_ratio_setting("MELONCLAW_AGENT_SUMMARY_TRIGGER_RATIO", 0.8),
        agent_summary_keep_tokens=_int_setting("MELONCLAW_AGENT_SUMMARY_KEEP_TOKENS", 4096),
        agent_model_call_limit=_int_setting("MELONCLAW_AGENT_MODEL_CALL_LIMIT", 30),
        agent_tool_call_limit=_int_setting("MELONCLAW_AGENT_TOOL_CALL_LIMIT", 100),
        agent_retry_max_retries=_int_setting("MELONCLAW_AGENT_RETRY_MAX_RETRIES", 2, minimum=0),
        agent_retry_initial_delay=_int_setting("MELONCLAW_AGENT_RETRY_INITIAL_DELAY", 1, minimum=0),
        agent_retry_max_delay=_int_setting("MELONCLAW_AGENT_RETRY_MAX_DELAY", 10, minimum=0),
        agent_usage_enabled=_bool_setting("MELONCLAW_AGENT_USAGE_ENABLED", True),
        agent_todo_enabled=_bool_setting("MELONCLAW_AGENT_TODO_ENABLED", True),
        agent_cache_entries=_int_setting("MELONCLAW_AGENT_CACHE_ENTRIES", 32),
        attachment_max_file_bytes=_int_setting(
            "MELONCLAW_ATTACHMENT_MAX_FILE_MB", 20
        )
        * 1024
        * 1024,
        attachment_max_per_message=_int_setting(
            "MELONCLAW_ATTACHMENT_MAX_PER_MESSAGE", 10
        ),
        attachment_max_total_bytes=_int_setting(
            "MELONCLAW_ATTACHMENT_MAX_TOTAL_MB", 50
        )
        * 1024
        * 1024,
        attachment_project_max_bytes=_int_setting(
            "MELONCLAW_ATTACHMENT_PROJECT_MAX_MB", 1024
        )
        * 1024
        * 1024,
        attachment_image_max_pixels=_int_setting(
            "MELONCLAW_ATTACHMENT_IMAGE_MAX_PIXELS", 30_000_000
        ),
        attachment_pdf_max_pages=_int_setting(
            "MELONCLAW_ATTACHMENT_PDF_MAX_PAGES", 500
        ),
        attachment_parse_max_chars=_int_setting(
            "MELONCLAW_ATTACHMENT_PARSE_MAX_CHARS", 2_000_000
        ),
        attachment_archive_max_entries=_int_setting(
            "MELONCLAW_ATTACHMENT_ARCHIVE_MAX_ENTRIES", 5_000
        ),
        attachment_archive_max_uncompressed_bytes=_int_setting(
            "MELONCLAW_ATTACHMENT_ARCHIVE_MAX_UNCOMPRESSED_MB", 200
        )
        * 1024
        * 1024,
        attachment_archive_max_entry_bytes=_int_setting(
            "MELONCLAW_ATTACHMENT_ARCHIVE_MAX_ENTRY_MB", 50
        )
        * 1024
        * 1024,
        attachment_archive_max_compression_ratio=_int_setting(
            "MELONCLAW_ATTACHMENT_MAX_COMPRESSION_RATIO", 50
        ),
        attachment_staged_ttl_hours=_int_setting(
            "MELONCLAW_ATTACHMENT_STAGED_TTL_HOURS", 24
        ),
        attachment_parse_concurrency=_int_setting(
            "MELONCLAW_ATTACHMENT_PARSE_CONCURRENCY", 2
        ),
        attachment_validation_timeout_seconds=_int_setting(
            "MELONCLAW_ATTACHMENT_VALIDATE_TIMEOUT_SECONDS", 30
        ),
        attachment_parse_timeout_seconds=_int_setting(
            "MELONCLAW_ATTACHMENT_PARSE_TIMEOUT_SECONDS", 300
        ),
        attachment_parse_lease_seconds=_int_setting(
            "MELONCLAW_ATTACHMENT_PARSE_LEASE_SECONDS", 60
        ),
        attachment_parse_max_attempts=_int_setting(
            "MELONCLAW_ATTACHMENT_PARSE_MAX_ATTEMPTS", 3
        ),
        attachment_image_outbound_max_edge=_int_setting(
            "MELONCLAW_ATTACHMENT_IMAGE_MAX_EDGE", 1568, minimum=64
        ),
        attachment_image_outbound_jpeg_quality=_int_setting(
            "MELONCLAW_ATTACHMENT_IMAGE_JPEG_QUALITY", 85, minimum=1
        ),
        attachment_image_cache_entries=_int_setting(
            "MELONCLAW_ATTACHMENT_IMAGE_CACHE_ENTRIES", 32
        ),
        user_input_ttl_seconds=_int_setting(
            "MELONCLAW_USER_INPUT_TTL_SECONDS", 24 * 60 * 60
        ),
    )


def provider_env_key(name: str) -> str | None:
    """只解析明确登记的模型凭据变量，绝不输出凭据。"""
    import re

    if not name or not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:_API_KEY|_ACCESS_TOKEN)", name):
        return None
    return os.getenv(name, "").strip() or None


def allowed_origins() -> list[str]:
    """CORS 与 Cookie 写请求共用的精确来源白名单。"""
    raw = os.getenv("MELONCLAW_ALLOWED_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("Cookie 来源配置必须使用明确的 Origin，不能使用通配符。")
    return origins or ["http://localhost:8001", "http://127.0.0.1:8001"]
