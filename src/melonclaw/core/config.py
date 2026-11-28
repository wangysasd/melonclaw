"""应用运行配置；聊天/解析模型由数据库提供，固定嵌入凭据由开发侧提供。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_REPORT_WARNED: set[str] = set()
_REPORT_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


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


def _audit_dir(data_root: Path) -> Path:
    """安全审计目录。未单独配置时放在数据根下，不使用 14 天应用日志。"""

    configured = os.getenv("MELONCLAW_AUDIT_DIR", "").strip()
    if not configured:
        return data_root / "audit"
    return _validate_persistent_root(Path(configured).expanduser())


def _webhook_url(value: str) -> bool:
    """只接受不含账号口令的 http(s) 地址。失败时不回显原值。"""

    if not value or len(value) > 2000 or any(ord(char) < 33 or char.isspace() for char in value):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
    )


def _audit_alert_config() -> dict[str, object]:
    """审计写入失败的接收地址。留空表示只保留应用警告。"""

    timeout = _int_setting("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS", 2)
    retry = _int_setting("MELONCLAW_AUDIT_ALERT_RETRY_SECONDS", 300)
    if timeout > 30:
        raise RuntimeError("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS 不能大于 30。")
    if retry > 86400:
        raise RuntimeError("MELONCLAW_AUDIT_ALERT_RETRY_SECONDS 不能大于 86400。")
    if timeout >= retry:
        raise RuntimeError("审计告警超时必须小于重试间隔。")
    raw = os.getenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", "").strip()
    if raw and not _webhook_url(raw):
        raise RuntimeError("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL 必须是 http 或 https 地址。")
    return {
        "audit_alert_webhook_url": raw,
        "audit_alert_timeout_seconds": timeout,
        "audit_alert_retry_seconds": retry,
    }


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
    """应用运行配置；聊天配置只来自数据库，固定嵌入凭据由开发侧提供。"""

    workspace_root: Path
    assistant_name: str = "MelonClaw"
    operator_name: str = ""
    profile: str = ""
    jwt_secret: str = field(default="", repr=False)
    token_ttl_hours: int = 1000
    data_root: Path = field(default_factory=_create_data_root)
    audit_dir: Path | None = None
    audit_alert_webhook_url: str = field(default="", repr=False)
    audit_alert_timeout_seconds: int = 2
    audit_alert_retry_seconds: int = 300
    database_url: str = field(default="", repr=False)
    deepseek_api_key: str = field(default="", repr=False)
    knowledge_embedding_api_key: str = field(default="", repr=False)
    knowledge_embedding_url: str = ""
    knowledge_embedding_model_name: str = ""
    knowledge_embedding_dimensions: int = 0
    knowledge_max_file_bytes: int = 52428800
    knowledge_max_pdf_pages: int = 100
    knowledge_max_chars: int = 2000000
    knowledge_concurrency: int = 2
    knowledge_parse_timeout_seconds: int = 120
    knowledge_job_timeout_seconds: int = 3600
    knowledge_ocr_timeout_seconds: int = 180
    knowledge_vector_min_score: float = 0.4
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
    agent_model_call_limit: int = 1000
    agent_tool_call_limit: int = 5000
    agent_retry_max_retries: int = 2
    agent_retry_initial_delay: int = 1
    agent_retry_max_delay: int = 10
    agent_usage_enabled: bool = True
    agent_todo_enabled: bool = True
    agent_cache_entries: int = 32
    user_input_ttl_seconds: int = 24 * 60 * 60
    task_execution_timeout_seconds: int = 30 * 60
    task_result_max_chars: int = 200_000
    task_history_timeout_seconds: int = 60
    task_history_summary_max_calls: int = 4
    sms_enabled: bool = False
    registration_enabled: bool = False
    sms_access_key_id: str = field(default="", repr=False)
    sms_access_key_secret: str = field(default="", repr=False)
    sms_sign_name: str = ""
    sms_template_auth: str = ""
    sms_template_bind: str = ""
    sms_template_reset: str = ""
    sms_code_variable: str = "code"
    sms_minutes_variable: str = "min"
    phone_hmac_key: str = field(default="", repr=False)
    sms_phone_interval_seconds: int = 60
    sms_phone_daily_limit: int = 10
    sms_ip_daily_limit: int = 30
    sms_global_daily_limit: int = 200
    sms_max_attempts: int = 5
    sms_code_ttl_seconds: int = 300
    sms_timeout_seconds: int = 8

    langfuse_enabled: bool = False
    langfuse_public_key: str = field(default="", repr=False)
    langfuse_secret_key: str = field(default="", repr=False)
    langfuse_base_url: str = field(default="", repr=False)
    langfuse_environment: str = "development"
    langfuse_capture_content: bool = True

    task_poll_seconds: int = 2
    task_global_concurrency: int = 2
    task_user_concurrency: int = 1
    task_queue_timeout_seconds: int = 86400
    task_checkpoint_retention_days: int = 30
    task_operation_retention_days: int = 30
    task_cleanup_interval_seconds: int = 3600
    report_contact_email: str = ""
    report_contact_name: str = ""
    report_response_hours: int | None = None

    def __post_init__(self) -> None:
        if min(self.task_poll_seconds, self.task_global_concurrency, self.task_user_concurrency,
               self.task_queue_timeout_seconds, self.task_checkpoint_retention_days,
               self.task_cleanup_interval_seconds, self.task_operation_retention_days,
               self.task_history_timeout_seconds, self.task_history_summary_max_calls) < 1:
            raise ValueError("任务调度参数必须为正数。")
        if not 0 < self.agent_summary_trigger_ratio < 1:
            raise ValueError("摘要触发比例必须大于 0 且小于 1。")
        if self.agent_output_reserve <= 0 or self.agent_summary_keep_tokens <= 0:
            raise ValueError("输出预留和摘要保留 token 必须大于 0。")
        if self.agent_retry_max_delay < self.agent_retry_initial_delay:
            raise ValueError("最大重试间隔不能小于初始间隔。")
        if min(self.task_execution_timeout_seconds, self.task_result_max_chars) < 1:
            raise ValueError("任务时限和结果上限必须为正数。")
        if (
            self.audit_alert_timeout_seconds < 1
            or self.audit_alert_retry_seconds < 1
            or self.audit_alert_timeout_seconds > 30
            or self.audit_alert_retry_seconds > 86400
            or self.audit_alert_timeout_seconds >= self.audit_alert_retry_seconds
        ):
            raise ValueError("审计告警超时必须小于重试间隔，并且都在允许范围内。")
        if self.audit_alert_webhook_url and not _webhook_url(self.audit_alert_webhook_url):
            raise ValueError("审计告警接收地址无效。")

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


def _jwt_secret() -> str:
    raw = os.getenv("MELONCLAW_JWT_SECRET", "").strip()
    if len(raw) < 32:
        raise RuntimeError("MELONCLAW_JWT_SECRET 必须配置且至少 32 个字符。")
    return raw


def _token_ttl_hours() -> int:
    raw = os.getenv("MELONCLAW_TOKEN_TTL_HOURS")
    if raw is None or not raw.strip():
        return 1000
    return _int_setting("MELONCLAW_TOKEN_TTL_HOURS", 1000)


def jwt_settings() -> tuple[str, int]:
    """只校验 JWT 配置，不初始化应用目录或数据库。"""
    return _jwt_secret(), _token_ttl_hours()


def _operator_name() -> str:
    """公开运营主体。留空可以启动；控制字符或超长时拒绝启动。"""

    name = os.getenv("MELONCLAW_OPERATOR_NAME", "").strip()
    if re.search(r"[\x00-\x1f\x7f]", name):
        raise RuntimeError("MELONCLAW_OPERATOR_NAME 含有无效字符。")
    if len(name) > 80:
        raise RuntimeError("MELONCLAW_OPERATOR_NAME 过长。")
    return name


def _variable_name(name: str, default: str) -> str:
    raw = os.getenv(name, default).strip() or default
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,20}", raw) is None:
        raise RuntimeError(f"{name} 格式无效。")
    return raw


def _sms_config() -> dict[str, object]:
    """短信关闭时不要求云凭据，避免未开通的部署无法启动。"""

    enabled = _bool_setting("MELONCLAW_SMS_ENABLED", False)
    registration = _bool_setting("MELONCLAW_REGISTRATION_ENABLED", False)
    if not enabled:
        if registration:
            raise RuntimeError("MELONCLAW_REGISTRATION_ENABLED 需要同时启用 MELONCLAW_SMS_ENABLED。")
        return {"sms_enabled": False, "registration_enabled": False}
    required = {
        "MELONCLAW_SMS_ACCESS_KEY_ID": os.getenv("MELONCLAW_SMS_ACCESS_KEY_ID", "").strip(),
        "MELONCLAW_SMS_ACCESS_KEY_SECRET": os.getenv("MELONCLAW_SMS_ACCESS_KEY_SECRET", "").strip(),
        "MELONCLAW_SMS_SIGN_NAME": os.getenv("MELONCLAW_SMS_SIGN_NAME", "").strip(),
        "MELONCLAW_SMS_TEMPLATE_AUTH": os.getenv("MELONCLAW_SMS_TEMPLATE_AUTH", "").strip(),
        "MELONCLAW_SMS_TEMPLATE_BIND": os.getenv("MELONCLAW_SMS_TEMPLATE_BIND", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("短信已启用，但缺少配置：" + "、".join(missing) + "。")
    if len(required["MELONCLAW_SMS_SIGN_NAME"]) > 40:
        raise RuntimeError("MELONCLAW_SMS_SIGN_NAME 过长。")
    hmac_key = os.getenv("MELONCLAW_PHONE_HMAC_KEY", "").strip()
    if len(hmac_key) < 32:
        raise RuntimeError("MELONCLAW_PHONE_HMAC_KEY 必须配置且至少 32 个字符。")
    interval = _int_setting("MELONCLAW_SMS_PHONE_INTERVAL_SECONDS", 60)
    phone_daily = _int_setting("MELONCLAW_SMS_PHONE_DAILY_LIMIT", 10)
    ip_daily = _int_setting("MELONCLAW_SMS_IP_DAILY_LIMIT", 30)
    global_daily = _int_setting("MELONCLAW_SMS_GLOBAL_DAILY_LIMIT", 200)
    attempts = _int_setting("MELONCLAW_SMS_MAX_ATTEMPTS", 5)
    if attempts > 10:
        raise RuntimeError("MELONCLAW_SMS_MAX_ATTEMPTS 不能大于 10。")
    ttl = _int_setting("MELONCLAW_SMS_CODE_TTL_SECONDS", 300)
    if not 60 <= ttl <= 1800:
        raise RuntimeError("MELONCLAW_SMS_CODE_TTL_SECONDS 必须在 60 到 1800 之间。")
    timeout = _int_setting("MELONCLAW_SMS_TIMEOUT_SECONDS", 8)
    if timeout > 30:
        raise RuntimeError("MELONCLAW_SMS_TIMEOUT_SECONDS 不能大于 30。")
    return {
        "sms_enabled": True,
        "registration_enabled": registration,
        "sms_access_key_id": required["MELONCLAW_SMS_ACCESS_KEY_ID"],
        "sms_access_key_secret": required["MELONCLAW_SMS_ACCESS_KEY_SECRET"],
        "sms_sign_name": required["MELONCLAW_SMS_SIGN_NAME"],
        "sms_template_auth": required["MELONCLAW_SMS_TEMPLATE_AUTH"],
        "sms_template_bind": required["MELONCLAW_SMS_TEMPLATE_BIND"],
        "sms_template_reset": (
            os.getenv("MELONCLAW_SMS_TEMPLATE_RESET", "").strip()
            or required["MELONCLAW_SMS_TEMPLATE_AUTH"]
        ),
        "sms_code_variable": _variable_name("MELONCLAW_SMS_CODE_VARIABLE", "code"),
        "sms_minutes_variable": _variable_name("MELONCLAW_SMS_MINUTES_VARIABLE", "min"),
        "phone_hmac_key": hmac_key,
        "sms_phone_interval_seconds": interval,
        "sms_phone_daily_limit": phone_daily,
        "sms_ip_daily_limit": ip_daily,
        "sms_global_daily_limit": global_daily,
        "sms_max_attempts": attempts,
        "sms_code_ttl_seconds": ttl,
        "sms_timeout_seconds": timeout,
    }


def load_settings() -> Settings:
    """读取应用环境配置。"""

    jwt_secret, token_ttl_hours = jwt_settings()
    data_root = _create_data_root()
    sms = _sms_config()
    return Settings(
        workspace_root=_create_workspace_root(),
        assistant_name=os.getenv("MELONCLAW_NAME", "").strip() or "MelonClaw",
        operator_name=_operator_name(),
        profile=os.getenv("profile", ""),
        jwt_secret=jwt_secret,
        token_ttl_hours=token_ttl_hours,
        data_root=data_root,
        audit_dir=_audit_dir(data_root),
        database_url=os.getenv("DATABASE_URL", ""),
        **_audit_alert_config(),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        knowledge_embedding_api_key=os.getenv("KNOWLEDGE_EMBEDDING_API_KEY", "").strip(),
        knowledge_embedding_url=os.getenv("KNOWLEDGE_EMBEDDING_URL", "").strip(),
        knowledge_embedding_model_name=os.getenv("KNOWLEDGE_EMBEDDING_MODEL_NAME", "").strip(),
        knowledge_embedding_dimensions=_int_setting("KNOWLEDGE_EMBEDDING_DIMENSIONS", 0, minimum=0),
        knowledge_max_file_bytes=_int_setting("KNOWLEDGE_MAX_FILE_BYTES", 52428800),
        knowledge_max_pdf_pages=_int_setting("KNOWLEDGE_MAX_PDF_PAGES", 100),
        knowledge_max_chars=_int_setting("KNOWLEDGE_MAX_CHARS", 2000000),
        knowledge_concurrency=_int_setting("KNOWLEDGE_CONCURRENCY", 2),
        knowledge_parse_timeout_seconds=_int_setting("KNOWLEDGE_PARSE_TIMEOUT_SECONDS", 120),
        knowledge_job_timeout_seconds=_int_setting("KNOWLEDGE_JOB_TIMEOUT_SECONDS", 3600),
        knowledge_ocr_timeout_seconds=_int_setting("KNOWLEDGE_OCR_TIMEOUT_SECONDS", 180),
        knowledge_vector_min_score=_ratio_setting("KNOWLEDGE_VECTOR_MIN_SCORE", 0.4),
        langfuse_enabled=_bool_setting("LANGFUSE_TRACING_ENABLED", False),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        langfuse_base_url=os.getenv("LANGFUSE_BASE_URL", "").strip(),
        langfuse_environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development").strip(),
        langfuse_capture_content=_bool_setting("MELONCLAW_LANGFUSE_CAPTURE_CONTENT", True),
        tool_selection_timeout_seconds=_int_setting("MELONCLAW_TOOL_SELECTION_TIMEOUT_SECONDS", 10),
        tool_pool_size=_int_setting("MELONCLAW_TOOL_POOL_SIZE", 16),
        tool_selection_size=_int_setting("MELONCLAW_TOOL_SELECTION_SIZE", 8),
        tool_selection_max_requests=_int_setting("MELONCLAW_TOOL_SELECTION_MAX_REQUESTS", 4),
        agent_output_reserve=_int_setting("MELONCLAW_AGENT_OUTPUT_RESERVE", 4096),
        agent_summary_trigger_ratio=_ratio_setting("MELONCLAW_AGENT_SUMMARY_TRIGGER_RATIO", 0.8),
        agent_summary_keep_tokens=_int_setting("MELONCLAW_AGENT_SUMMARY_KEEP_TOKENS", 4096),
        agent_model_call_limit=_int_setting("MELONCLAW_AGENT_MODEL_CALL_LIMIT", 1000),
        agent_tool_call_limit=_int_setting("MELONCLAW_AGENT_TOOL_CALL_LIMIT", 5000),
        agent_retry_max_retries=_int_setting("MELONCLAW_AGENT_RETRY_MAX_RETRIES", 2, minimum=0),
        agent_retry_initial_delay=_int_setting("MELONCLAW_AGENT_RETRY_INITIAL_DELAY", 1, minimum=0),
        agent_retry_max_delay=_int_setting("MELONCLAW_AGENT_RETRY_MAX_DELAY", 10, minimum=0),
        agent_usage_enabled=_bool_setting("MELONCLAW_AGENT_USAGE_ENABLED", True),
        agent_todo_enabled=_bool_setting("MELONCLAW_AGENT_TODO_ENABLED", True),
        agent_cache_entries=_int_setting("MELONCLAW_AGENT_CACHE_ENTRIES", 32),
        task_poll_seconds=_int_setting("MELONCLAW_TASK_POLL_SECONDS", 2),
        task_global_concurrency=_int_setting("MELONCLAW_TASK_GLOBAL_CONCURRENCY", 2),
        task_user_concurrency=_int_setting("MELONCLAW_TASK_USER_CONCURRENCY", 1),
        task_checkpoint_retention_days=_int_setting("MELONCLAW_TASK_CHECKPOINT_RETENTION_DAYS", 30),
        task_operation_retention_days=_int_setting("MELONCLAW_TASK_OPERATION_RETENTION_DAYS", 30),
        task_history_timeout_seconds=_int_setting("MELONCLAW_TASK_HISTORY_TIMEOUT_SECONDS", 60),
        task_history_summary_max_calls=_int_setting("MELONCLAW_TASK_HISTORY_SUMMARY_MAX_CALLS", 4),
        task_cleanup_interval_seconds=_int_setting("MELONCLAW_TASK_CLEANUP_INTERVAL_SECONDS", 3600),
        task_queue_timeout_seconds=_int_setting("MELONCLAW_TASK_QUEUE_TIMEOUT_SECONDS", 86400),
        task_execution_timeout_seconds=_int_setting("MELONCLAW_TASK_EXECUTION_TIMEOUT_SECONDS", 1800),
        task_result_max_chars=_int_setting("MELONCLAW_TASK_RESULT_MAX_CHARS", 200_000),
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
        report_contact_email=_report_email(),
        report_contact_name=_report_name(),
        report_response_hours=_report_hours(),
        **sms,
    )


def _warn_report_once(key: str, message: str) -> None:
    if key in _REPORT_WARNED:
        return
    _REPORT_WARNED.add(key)
    logger.warning(message)


def _report_email() -> str:
    raw = os.getenv("MELONCLAW_REPORT_CONTACT_EMAIL", "").strip()
    if not raw:
        return ""
    if len(raw) > 120 or _REPORT_EMAIL.fullmatch(raw) is None:
        _warn_report_once("email", "MELONCLAW_REPORT_CONTACT_EMAIL 无效，投诉页不展示邮箱。")
        return ""
    return raw


def _report_name() -> str:
    raw = os.getenv("MELONCLAW_REPORT_CONTACT_NAME", "").strip()
    if not raw:
        return ""
    if len(raw) > 40 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        _warn_report_once("name", "MELONCLAW_REPORT_CONTACT_NAME 无效，投诉页不展示联系人。")
        return ""
    return raw


def _report_hours() -> int | None:
    raw = os.getenv("MELONCLAW_REPORT_RESPONSE_HOURS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= 720:
        _warn_report_once("hours", "MELONCLAW_REPORT_RESPONSE_HOURS 无效，投诉页不展示响应时限。")
        return None
    return value


def provider_env_key(name: str) -> str | None:
    """只解析明确登记的模型凭据变量，绝不输出凭据。"""
    import re

    if not name or not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:_API_KEY|_ACCESS_TOKEN)", name):
        return None
    return os.getenv(name, "").strip() or None


def web_bind() -> tuple[str, int]:
    """解析 Web 进程监听地址。启动入口只读这两项，不因此创建数据目录。"""

    host = os.getenv("MELONCLAW_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("MELONCLAW_PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("MELONCLAW_PORT 必须是整数。") from exc
    return host, port


def allowed_origins() -> list[str]:
    """CORS 与 Cookie 写请求共用的精确来源白名单。"""
    raw = os.getenv("MELONCLAW_ALLOWED_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("Cookie 来源配置必须使用明确的 Origin，不能使用通配符。")
    return origins or ["http://localhost:8001", "http://127.0.0.1:8001"]


def configure_langfuse_environment() -> None:
    """应用禁止 SDK 自动上传媒体；环境变量写入仍由配置入口统一处理。"""
    os.environ["LANGFUSE_MEDIA_UPLOAD_ENABLED"] = "false"
