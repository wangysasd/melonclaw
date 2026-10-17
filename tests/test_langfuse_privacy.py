"""Offline privacy boundary tests for Langfuse's real SDK export path."""

import asyncio
import json
import logging
from uuid import uuid4

import httpx
import langfuse
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from melonclaw.core.config import Settings
from melonclaw.core.tracing import Tracing, trace_config


def _settings(tmp_path, *, capture_content=True):
    return Settings(
        workspace_root=tmp_path,
        data_root=tmp_path / "data",
        langfuse_enabled=True,
        langfuse_public_key=f"pk-lf-test-{uuid4()}",
        langfuse_secret_key="fixture-secret",
        langfuse_base_url="http://localhost:1",
        langfuse_environment="test",
        langfuse_capture_content=capture_content,
    )


@pytest.fixture
def sdk_factory(monkeypatch):
    """Construct the actual SDK client while directing export into an offline exporter."""
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    original = langfuse.Langfuse

    def create(exporter, **client_options):
        monkeypatch.setattr("melonclaw.core.trace_exporter.otlp_exporter", lambda settings: exporter)
        monkeypatch.setattr(
            langfuse, "Langfuse", lambda **kwargs: original(**kwargs, **client_options),
        )
        return original

    return create


def _trace_args(**overrides):
    args = {
        "feature": "privacy-boundary",
        "user_id": "user-fixture",
        "tenant_id": "tenant-fixture",
        "conversation_id": "conversation-fixture",
        "run_id": str(uuid4()),
        "input": "safe question",
    }
    args.update(overrides)
    return args


def _flush(tracing, exporter):
    tracing.client.flush()
    return exporter.get_finished_spans()


def test_real_export_masks_arbitrary_objects_nested_json_and_credentials(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None

    class UnserializableRuntimeObject:
        def __repr__(self):
            raise AssertionError("telemetry must not invoke an arbitrary repr")

    secrets = (
        "authorization-private-credential",
        "cookie-private-session",
        "nested-json-private-token",
        "db-private-password",
    )
    payload = {
        "opaque": UnserializableRuntimeObject(),
        "request": {
            "headers": {
                "Authorization": f"Bearer {secrets[0]}",
                "Cookie": f"session={secrets[1]}",
            },
            "url": "https://url-user:url-private-password@example.invalid/api?token=url-private-token",
        },
        "tool_result": json.dumps({
            "nested": [{"access_token": secrets[2]}, {"database_url": f"postgres://db:{secrets[3]}@db.invalid/app"}],
        }),
    }

    try:
        with tracing.run(**_trace_args(input=payload)) as run:
            run.finish(output=payload)
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events, span.status) for span in spans])
        assert spans, "the real Langfuse SDK should export the observation to the injected exporter"
        for secret in secrets:
            assert secret not in wire
        for secret in ("url-private-password", "url-private-token"):
            assert secret not in wire
        assert "UnserializableRuntimeObject" not in wire
    finally:
        asyncio.run(tracing.close())


def test_callback_error_is_exported_as_otel_error_without_private_status_text(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None
    private = "error-private-secret"

    try:
        with tracing.run(**_trace_args()) as run:
            callback = run.config["callbacks"][0]
            run_id = uuid4()
            callback.on_chain_start(None, {"input": "ordinary"}, run_id=run_id, name="failure-check")
            callback.on_chain_error(
                RuntimeError(
                    f"request failed Authorization: Bearer {private}; "
                    "Cookie: sid=cookie-private; "
                    "POST https://alice:url-private-password@example.invalid/run"
                ),
                run_id=run_id,
            )
            run.finish(status="failed")

        spans = _flush(tracing, exporter)
        failure_span = next(span for span in spans if span.name == "failure-check")
        assert failure_span.status.status_code.name == "ERROR"
        wire = repr([(span.attributes, span.events, span.status) for span in spans])
        for forbidden in (private, "cookie-private", "url-private-password"):
            assert forbidden not in wire
        root = next(span for span in spans if span.name == "melonclaw-privacy-boundary")
        assert root.attributes["langfuse.observation.status_message"] == "failed"
        assert root.status.status_code.name == "ERROR"
    finally:
        asyncio.run(tracing.close())


def test_capture_content_false_preserves_model_and_usage_but_exports_no_prompts_or_answers(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path, capture_content=False))
    assert tracing.client is not None
    question = "content-opt-out-private-question"
    answer = "content-opt-out-private-answer"

    async def execute():
        with tracing.run(**_trace_args(input=question)):
            model = FakeMessagesListChatModel(responses=[AIMessage(
                content=answer,
                usage_metadata={"input_tokens": 13, "output_tokens": 5, "total_tokens": 18},
                response_metadata={"model_name": "fixture-model-private-content"},
            )])
            await model.ainvoke(
                [HumanMessage(content=question)],
                config=trace_config({"metadata": {"ls_model_name": "fixture-model-private-content"}}),
            )

    try:
        asyncio.run(execute())
        spans = _flush(tracing, exporter)
        generation = next(
            span for span in spans
            if span.attributes.get("langfuse.observation.type") == "generation"
        )
        serialized = repr([(span.attributes, span.events, span.status) for span in spans])
        assert question not in serialized and answer not in serialized
        assert generation.attributes["langfuse.observation.model.name"] == "fixture-model-private-content"
        usage = json.loads(generation.attributes["langfuse.observation.usage_details"])
        assert usage["input"] == 13
        assert usage["output"] == 5
        assert usage["total"] == 18
    finally:
        asyncio.run(tracing.close())


def test_exporter_failure_is_nonfatal_and_sdk_logs_do_not_expose_response_body(
    tmp_path, sdk_factory, caplog,
):
    body_secret = "offline-export-body-private-secret"

    class FailingOfflineExporter(InMemorySpanExporter):
        def export(self, spans):
            logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").warning(
                "HTTP 503 response body: %s", body_secret,
            )
            raise RuntimeError(f"offline exporter rejected request: {body_secret}")

    exporter = FailingOfflineExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None
    caplog.set_level(logging.WARNING)

    try:
        # The application work and failure status must complete independently of telemetry.
        with tracing.run(**_trace_args()) as run:
            run.finish(output="business result", status="failed")
        with pytest.raises(ValueError, match="business failure"):
            with tracing.run(**_trace_args()):
                raise ValueError("business failure")
        try:
            tracing.client.flush()
        except Exception as exc:  # SDK versions may surface exporter failures synchronously.
            assert body_secret not in str(exc)

        assert body_secret not in caplog.text
        assert "Langfuse telemetry diagnostic" in caplog.text or not caplog.records
    finally:
        # Shutdown failures are also deliberately swallowed by the application boundary.
        asyncio.run(tracing.close())


def test_langfuse_initialization_failure_logs_no_sdk_exception_details(tmp_path, monkeypatch, caplog):
    private = "initialization-private-secret"

    def fail_client(**kwargs):
        raise RuntimeError(f"bad endpoint token={private} https://user:{private}@example.invalid")

    monkeypatch.setattr(langfuse, "Langfuse", fail_client)
    caplog.set_level(logging.WARNING)
    tracing = Tracing(_settings(tmp_path))
    try:
        assert tracing.client is None
        with tracing.run(**_trace_args()) as run:
            assert run.span is None
            assert not trace_config().get("callbacks")
        assert private not in caplog.text
        assert "Langfuse 初始化失败" in caplog.text
    finally:
        asyncio.run(tracing.close())


def test_export_hook_redacts_known_secrets_from_otel_sequence_attributes(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    settings = _settings(tmp_path)
    tracing = Tracing(settings)
    assert tracing.client is not None
    private = settings.langfuse_secret_key
    bearer = "sequence-private-bearer-token"

    try:
        with tracing.run(**_trace_args()) as run:
            # Add an attribute after Langfuse's observation API has already applied its
            # creation-time mask. This exercises the final OTel exporter hook directly.
            run.span._otel_span.set_attribute(
                "langfuse.observation.metadata.sequence",
                [private, f"Authorization: Bearer {bearer}", "Cookie: sid=sequence-cookie-value"],
            )
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events, span.status) for span in spans])
        for forbidden in (private, bearer, "sequence-cookie-value"):
            assert forbidden not in wire
        assert "[REDACTED]" in wire
    finally:
        asyncio.run(tracing.close())


def test_capture_content_false_hides_arbitrary_metadata_parameters_status_and_events(
    tmp_path, sdk_factory,
):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path, capture_content=False))
    assert tracing.client is not None
    private_values = (
        "metadata-opt-out-private-value",
        "parameter-opt-out-private-value",
        "status-opt-out-private-value",
        "event-opt-out-private-value",
    )

    try:
        with tracing.run(**_trace_args(metadata={"feature": "expected-feature", "custom_note": private_values[0]})) as run:
            with tracing.client.start_as_current_observation(
                name="opt-out-generation",
                as_type="generation",
                input="generation-private-input",
                output="generation-private-output",
                model="fixture-model-retained",
                model_parameters={"temperature": 0.4, "private_option": private_values[1]},
                usage_details={"input": 9, "output": 3},
                metadata={"custom_note": private_values[0], "feature": "expected-feature"},
            ):
                pass
            run.finish(status="failed")
            # Model a lower-level OTel exception event/status path. These are outside the
            # Langfuse attribute map and can reveal whether the export mask covers them.
            run.span._otel_span.set_status(Status(StatusCode.ERROR, description=private_values[2]))
            run.span._otel_span.add_event(
                "exception", {"exception.message": private_values[3], "exception.type": "RuntimeError"},
            )

        spans = _flush(tracing, exporter)
        generation = next(span for span in spans if span.name == "opt-out-generation")
        wire = repr([(span.attributes, span.events, span.status) for span in spans])
        root = next(span for span in spans if span.name == "melonclaw-privacy-boundary")
        event_text = repr([(event.name, event.attributes) for span in spans for event in span.events])
        leaked = [value for value in (*private_values, "generation-private-input", "generation-private-output")
                  if value in wire or value in event_text or value == root.status.description]
        assert not leaked, f"content opt-out leaked through exported attributes, OTel status, or events: {leaked}"
        assert generation.attributes["langfuse.observation.model.name"] == "fixture-model-retained"
        usage = json.loads(generation.attributes["langfuse.observation.usage_details"])
        assert usage == {"input": 9, "output": 3}
        assert "expected-feature" in wire
    finally:
        asyncio.run(tracing.close())


def test_media_data_uris_are_redacted_before_export_without_http_upload(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"mediaId": "offline-fixture", "uploadUrl": "http://localhost/upload"})

    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    sdk_factory(exporter, httpx_client=http_client)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None
    encoded = {
        "image_url": "data:image/png;base64,IMAGE-PRIVATE-BASE64-CONTENT",
        "file_data": "data:application/pdf;base64,FILE-PRIVATE-BASE64-CONTENT",
        "audio": "data:audio/wav;base64,AUDIO-PRIVATE-BASE64-CONTENT",
    }

    try:
        with tracing.run(**_trace_args(input={"content": [encoded]})) as run:
            run.finish(output=encoded)
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events) for span in spans])
        assert spans
        for marker in ("IMAGE-PRIVATE-BASE64-CONTENT", "FILE-PRIVATE-BASE64-CONTENT", "AUDIO-PRIVATE-BASE64-CONTENT"):
            assert marker not in wire
        assert not requests, f"privacy opt-out/redaction must prevent media HTTP requests; got {[r.url.path for r in requests]}"
    finally:
        asyncio.run(tracing.close())
        http_client.close()


def test_long_deep_and_cyclic_payloads_are_bounded_in_the_real_export_path(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None
    long_payload = "long-private-prefix-" + ("x" * 100_100) + "long-private-tail"
    deep = {"leaf": "deep-private-leaf"}
    cursor = deep
    for _ in range(30):
        cursor["next"] = {}
        cursor = cursor["next"]
    cursor["sensitive_value"] = "deep-private-tail-marker"
    cyclic = {"label": "cycle-root"}
    cyclic["self"] = cyclic

    try:
        with tracing.run(**_trace_args(input={"long": long_payload, "deep": deep, "cycle": cyclic})):
            pass
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events) for span in spans])
        assert spans
        assert "long-private-tail" not in wire
        assert "deep-private-tail-marker" not in wire
        assert "[TRUNCATED]" in wire
        assert "cycle-root" in wire
        assert len(wire) < 120_000, "oversized trace fields should be bounded before export"
    finally:
        asyncio.run(tracing.close())


def test_adjacent_raw_authorization_and_cookie_text_is_redacted_in_export(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None
    raw = (
        "Authorization: Bearer adjacent-bearer-private Cookie: sid=adjacent-cookie-private;"
        "Authorization: Basic adjacent-basic-private Cookie: csrf=adjacent-csrf-private"
    )

    try:
        with tracing.run(**_trace_args(input=raw)):
            pass
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events) for span in spans])
        for forbidden in (
            "adjacent-bearer-private",
            "adjacent-cookie-private",
            "adjacent-basic-private",
            "adjacent-csrf-private",
        ):
            assert forbidden not in wire
    finally:
        asyncio.run(tracing.close())


def test_unknown_dictionary_key_never_calls_its_repr_during_sdk_masking(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    assert tracing.client is not None

    class OpaqueKey:
        repr_calls = 0

        def __repr__(self):
            type(self).repr_calls += 1
            return "opaque-key"

    key = OpaqueKey()

    try:
        with tracing.run(**_trace_args(input={key: "key-private-value"})) as run:
            assert run.span is not None
        assert OpaqueKey.repr_calls == 0
        spans = _flush(tracing, exporter)
        wire = repr([(span.attributes, span.events) for span in spans])
        assert "key-private-value" not in wire
    finally:
        asyncio.run(tracing.close())


def test_business_data_survives_masking_while_media_data_is_hidden(tmp_path, sdk_factory):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    tracing = Tracing(_settings(tmp_path))
    payload = {"data": {"price": 123.45, "api_key": "business-private-key"},
               "image": {"type": "base64", "media_type": "image/png", "data": "image-raw-private-bytes"}}
    try:
        with tracing.run(**_trace_args(input=payload)) as run:
            run.finish(output=payload)
        spans = _flush(tracing, exporter)
        root = next(span for span in spans if span.name == "melonclaw-privacy-boundary")
        wire = repr(root.attributes)
        assert "123.45" in wire, "ordinary API result data must remain useful for debugging"
        assert "business-private-key" not in wire and "image-raw-private-bytes" not in wire
    finally:
        asyncio.run(tracing.close())


@pytest.mark.parametrize("capture_content", [True, False])
def test_otel_resource_environment_cannot_bypass_privacy(tmp_path, sdk_factory, monkeypatch, capture_content):
    exporter = InMemorySpanExporter()
    sdk_factory(exporter)
    private = "synthetic-resource-private-password"
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"db_password={private},custom_note=resource-private-note")
    tracing = Tracing(_settings(tmp_path, capture_content=capture_content))
    try:
        with tracing.run(**_trace_args()) as run:
            run.finish(output="safe")
        spans = _flush(tracing, exporter)
        assert spans
        wire = repr([span.resource.attributes for span in spans])
        assert private not in wire and "resource-private-note" not in wire
        assert all(span.resource.attributes["service.name"] == "melonclaw" for span in spans)
    finally:
        asyncio.run(tracing.close())
