"""应用运行配置；模型与凭据由数据库配置提供。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _create_workspace_root() -> Path:
    """返回跨进程保留的工作区根目录。"""

    configured = os.getenv("MELONCLAW_WORKSPACE_DIR", "").strip()
    workspace_root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".melonclaw" / "workspaces"
    )
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
        else Path(__file__).resolve().parents[3] / ".data"
    )
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


@dataclass(frozen=True)
class Settings:
    """应用运行配置，不包含模型连接或模型凭据。"""

    workspace_root: Path
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
    agent_cache_entries: int = 32
    user_input_ttl_seconds: int = 24 * 60 * 60

    @property
    def psycopg_database_url(self) -> str:
        """把业务 asyncpg URL 派生为 Checkpointer 使用的 psycopg URL。"""

        from melonclaw.database import derive_psycopg_database_url

        return derive_psycopg_database_url(self.database_url)

def load_settings() -> Settings:
    """读取应用环境配置。"""

    return Settings(
        workspace_root=_create_workspace_root(),
        database_url=os.getenv("DATABASE_URL", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
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
