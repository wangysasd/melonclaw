"""最终导出边界：SDK 属性钩子之外的 OTel 状态、事件和 links 也需要脱敏。"""

import base64
from importlib.metadata import version

from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Link, Status

from melonclaw.core.trace_privacy import redact_data, redact_text

# No automatic resource detectors: deployment-provided OTel attributes may
# contain arbitrary business text or credentials outside the SDK's mask hooks.
TRACE_RESOURCE = Resource({"service.name": "melonclaw"})


def otlp_exporter(settings):
    """显式使用应用配置，凭据只进入 HTTP 授权头。"""
    authorization = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    return OTLPSpanExporter(
        endpoint=f"{settings.langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {authorization}",
                 "x-langfuse-sdk-name": "python", "x-langfuse-sdk-version": version("langfuse"),
                 "x-langfuse-public-key": settings.langfuse_public_key,
                 "x-langfuse-ingestion-version": "4"},
        timeout=5, compression=Compression.Gzip,
    )


class PrivacySpanExporter(SpanExporter):
    def __init__(self, exporter, *, capture_content, secrets):
        self.exporter = exporter
        self.capture_content = capture_content
        self.secrets = secrets

    def export(self, spans):
        sanitized = []
        for span in spans:
            description = span.status.description
            if description:
                description = redact_text(description, self.secrets) if self.capture_content else None
            events = [Event(
                name=redact_text(event.name, self.secrets),
                attributes=redact_data(dict(event.attributes or {}), self.secrets),
                timestamp=event.timestamp,
            ) for event in span.events] if self.capture_content else []
            links = [Link(link.context, attributes=redact_data(dict(link.attributes or {}), self.secrets))
                     for link in span.links] if self.capture_content else []
            sanitized.append(ReadableSpan(
                name=redact_text(span.name, self.secrets), context=span.context, parent=span.parent,
                resource=TRACE_RESOURCE, attributes=span.attributes, events=events, links=links,
                kind=span.kind, status=Status(span.status.status_code, description),
                start_time=span.start_time, end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            ))
        return self.exporter.export(sanitized)

    def shutdown(self):
        return self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis=timeout_millis)
