"""可选 Langfuse 资源及每轮隔离的 LangChain 回调，不参与业务持久化。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from langchain_core.runnables.config import merge_configs

from melonclaw.core.config import Settings, configure_langfuse_environment
from melonclaw.core.trace_privacy import make_export_mask, redact_data

logger = logging.getLogger(__name__)


class _TelemetrySanitizer(logging.Handler):
    """先变换传播记录，再交给部署者已有的日志 handlers。"""
    def emit(self, record):
        pass


class _SafeTelemetryLogs(logging.Filter):
    def filter(self, record):
        record.msg = "Langfuse telemetry diagnostic; check tracing configuration and connectivity."
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return record.levelno >= logging.WARNING


def _protect_sdk_logs():
    # SDK/exporter failures can include raw exceptions or HTTP response bodies.
    for name in ("langfuse", "opentelemetry.exporter.otlp.proto.http.trace_exporter",
                 "opentelemetry.sdk._shared_internal"):
        sdk_logger = logging.getLogger(name)
        if not any(isinstance(f, _SafeTelemetryLogs) for f in sdk_logger.filters):
            sdk_logger.addFilter(_SafeTelemetryLogs())
        if not any(isinstance(handler, _TelemetrySanitizer) for handler in sdk_logger.handlers):
            handler = _TelemetrySanitizer()
            handler.addFilter(_SafeTelemetryLogs())
            sdk_logger.handlers.insert(0, handler)

_current_config: ContextVar[dict | None] = ContextVar("melonclaw_trace_config", default=None)


def trace_config(config: dict | None = None) -> dict:
    """在执行副本合并回调；不将监控写入 Checkpoint 或缓存 Agent。"""
    return merge_configs(config or {}, _current_config.get() or {})


@dataclass
class TraceRun:
    span: Any = None
    config: dict = field(default_factory=dict)
    finished: bool = False

    def finish(self, *, output: Any = None, status: str = "completed", error_code: str | None = None) -> None:
        self.finished = True
        if self.span is None:
            return
        try:
            self.span.update(
                output=output, metadata={"status": status, "error_code": error_code},
                level="ERROR" if status in {"failed", "cancelled"} else "DEFAULT",
                status_message=status,
            )
        except Exception:
            logger.warning("Langfuse 追踪更新失败，业务执行继续。")


class Tracing:
    def __init__(self, settings: Settings):
        self.client = None
        self.provider = None
        self.settings = settings
        if not settings.langfuse_enabled:
            return
        try:
            url = urlsplit(settings.langfuse_base_url)
        except ValueError:
            logger.warning("Langfuse 地址无效，已停用追踪。")
            return
        if not (settings.langfuse_public_key and settings.langfuse_secret_key
                and url.scheme in {"http", "https"} and url.hostname
                and not url.username and not url.password and not url.query and not url.fragment):
            logger.warning("Langfuse 配置不完整或地址无效，已停用追踪。")
            return
        try:
            # Import only after application settings and dotenv are loaded.
            from langfuse import Langfuse
            from opentelemetry.sdk.trace import TracerProvider

            from melonclaw.core.trace_exporter import (
                TRACE_RESOURCE,
                PrivacySpanExporter,
                otlp_exporter,
            )

            configure_langfuse_environment()
            _protect_sdk_logs()
            self.provider = TracerProvider(resource=TRACE_RESOURCE)
            secrets = (settings.langfuse_public_key, settings.langfuse_secret_key,
                       settings.database_url, settings.tavily_api_key)
            self.client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
                environment=settings.langfuse_environment,
                tracing_enabled=True, debug=False, timeout=5,
                sample_rate=1.0, flush_at=128, flush_interval=5,
                tracer_provider=self.provider,
                span_exporter=PrivacySpanExporter(
                    otlp_exporter(settings), capture_content=settings.langfuse_capture_content,
                    secrets=secrets,
                ),
                should_export_span=lambda span: span.instrumentation_scope.name == "langfuse-sdk",
                # The creation-time hook prevents arbitrary runtime objects being serialized;
                # the export hook also handles raw OTel attributes and content opt-out.
                mask=lambda *, data: redact_data(data, secrets),
                mask_otel_spans=make_export_mask(
                    capture_content=settings.langfuse_capture_content, secrets=secrets,
                ),
            )
        except Exception:
            logger.warning("Langfuse 初始化失败，已停用追踪；请检查监控配置。")

    @contextmanager
    def run(self, *, feature: str, user_id: str, tenant_id: str, conversation_id: str,
            run_id: str, input: Any, metadata: dict | None = None):
        run = TraceRun()
        stack = ExitStack()
        token = None
        try:
            if self.client is not None:
                from langfuse import propagate_attributes

                from melonclaw.core.trace_callbacks import TraceCallbackHandler

                attributes = {**(metadata or {}), "tenant_id": tenant_id,
                              "run_id": run_id, "feature": feature}
                stack.enter_context(propagate_attributes(
                    trace_name=f"melonclaw-{feature}", user_id=f"{tenant_id}:{user_id}",
                    session_id=f"{tenant_id}:{conversation_id}",
                    tags=["melonclaw", feature], metadata=attributes,
                ))
                run.span = stack.enter_context(self.client.start_as_current_observation(
                    name=f"melonclaw-{feature}", as_type="span", input=input,
                    trace_context={"trace_id": self.client.create_trace_id(seed=run_id)},
                ))
                run.config = {"callbacks": [TraceCallbackHandler(public_key=self.settings.langfuse_public_key,
                                                    capture_content=self.settings.langfuse_capture_content)],
                              "metadata": attributes}
                token = _current_config.set(run.config)
        except Exception:
            logger.warning("Langfuse 追踪创建失败，业务执行继续。")
        try:
            yield run
        except BaseException as exc:
            if not run.finished:
                run.finish(status="cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) else "failed")
            raise
        finally:
            if token is not None:
                _current_config.reset(token)
            try:
                # Do not record raw exceptions (which may contain URLs or credentials).
                stack.close()
            except Exception:
                logger.warning("Langfuse 追踪收尾失败，业务执行继续。")

    async def close(self) -> None:
        client, provider = self.client, self.provider
        self.client = self.provider = None

        def shutdown():
            try:
                if client is not None:
                    client.shutdown()
            finally:
                if provider is not None:
                    provider.shutdown()
        try:
            await asyncio.to_thread(shutdown)
        except Exception:
            logger.warning("Langfuse 停服上报未完成，请检查监控服务。")
