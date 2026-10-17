"""真实 SDK + 内存导出器：验证脱敏、异步隔离与实际图回调，不访问外网。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import langfuse
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from melonclaw.core.config import Settings, load_settings
from melonclaw.core.trace_privacy import redact_data
from melonclaw.core.tracing import Tracing, trace_config
from melonclaw.services.tracing import trace_chat_execution


@pytest.fixture
def telemetry(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    exporter = InMemorySpanExporter()
    monkeypatch.setattr("melonclaw.core.trace_exporter.otlp_exporter", lambda settings: exporter)
    settings = Settings(
        workspace_root=tmp_path, data_root=tmp_path / "data", langfuse_enabled=True,
        langfuse_public_key=f"pk-lf-test-{uuid4()}", langfuse_secret_key="test-secret",
        langfuse_base_url="http://localhost:1", langfuse_environment="test",
    )
    tracing = Tracing(settings)
    assert tracing.client is not None
    yield tracing, exporter
    asyncio.run(tracing.close())


def run_args(**kwargs):
    return dict(feature="chat-turn", user_id="test-user", tenant_id="test-tenant",
                conversation_id="test-conversation", run_id=str(uuid4()), input="hello", **kwargs)


def spans_after_flush(telemetry):
    tracing, exporter = telemetry
    tracing.client.flush()
    return exporter.get_finished_spans()


def test_disabled_and_invalid_configuration_do_not_create_client(monkeypatch, tmp_path):
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kw: pytest.fail("unexpected client"))
    settings = Settings(workspace_root=tmp_path)
    for candidate in (settings, replace(settings, langfuse_enabled=True)):
        tracing = Tracing(candidate)
        with tracing.run(**run_args()) as run:
            assert run.span is None
            assert not trace_config().get("callbacks")
        asyncio.run(tracing.close())


def test_configuration_keys_are_hidden_and_opt_out_is_parsed(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "private-secret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "private-public")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setenv("MELONCLAW_LANGFUSE_CAPTURE_CONTENT", "false")
    monkeypatch.setenv("MELONCLAW_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("MELONCLAW_DATA_DIR", str(tmp_path / "data"))
    settings = load_settings()
    assert not settings.langfuse_enabled and not settings.langfuse_capture_content
    assert "private-secret" not in repr(settings) and "private-public" not in repr(settings)


def test_export_retains_hierarchy_usage_and_redacts_secrets(telemetry):
    tracing, _ = telemetry
    secret = "sk-test-do-not-export-this"
    source = {"password": "sensitive", "message": "mail person@example.com phone 13800138000",
              "image_url": "data:image/png;base64,AAAA"}
    original = json.dumps(source)

    async def execute():
        with tracing.run(**run_args()) as run:
            model = FakeMessagesListChatModel(responses=[AIMessage(
                content=f"answer {secret}", usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                response_metadata={"model_name": "fixture-model"},
            )])
            await model.ainvoke([HumanMessage(content=json.dumps(source))], config=trace_config({"metadata": {"ls_model_name": "fixture-model"}}))
            run.finish(output="safe result")

    asyncio.run(execute())
    spans = spans_after_flush(telemetry)
    root = next(span for span in spans if span.name == "melonclaw-chat-turn")
    generation = next(span for span in spans if span.attributes.get("langfuse.observation.type") == "generation")
    assert generation.parent.span_id == root.context.span_id
    assert generation.attributes["langfuse.observation.model.name"] == "fixture-model"
    assert json.loads(generation.attributes["langfuse.observation.usage_details"])["input"] == 11
    assert generation.attributes["user.id"] == "test-tenant:test-user"
    assert generation.attributes["session.id"] == "test-tenant:test-conversation"
    exported = str([(span.attributes, span.status) for span in spans])
    for forbidden in (secret, "sensitive", "person@example.com", "13800138000", "AAAA"):
        assert forbidden not in exported
    assert json.dumps(source) == original


def test_concurrent_runs_use_distinct_handlers_and_reset_context(telemetry):
    tracing, _ = telemetry

    async def execute(index):
        with tracing.run(**run_args()) as run:
            config = trace_config({"configurable": {"thread_id": str(index)}})
            await asyncio.sleep(0)
            assert trace_config()["callbacks"][0] is config["callbacks"][0]
            assert config["configurable"]["thread_id"] == str(index)
            run.finish(output=index)
            return config["callbacks"][0]

    async def parallel():
        a, b = await asyncio.gather(execute(1), execute(2))
        assert a is not b
        assert not trace_config().get("callbacks")

    asyncio.run(parallel())
    roots = spans_after_flush(telemetry)
    assert len({span.context.trace_id for span in roots}) == 2


def test_monitoring_setup_and_update_errors_do_not_swallow_business_error(telemetry, monkeypatch):
    tracing, _ = telemetry
    monkeypatch.setattr(tracing.client, "start_as_current_observation", lambda **kw: (_ for _ in ()).throw(RuntimeError("sdk")))
    with tracing.run(**run_args()):
        assert not trace_config().get("callbacks")
    with pytest.raises(ValueError, match="business failure"):
        with tracing.run(**run_args()):
            raise ValueError("business failure")
    assert not trace_config().get("callbacks")


def test_errors_mask_otel_status_not_just_attributes(telemetry):
    tracing, _ = telemetry

    async def execute():
        with tracing.run(**run_args()) as run:
            callback = run.config["callbacks"][0]
            call_id = uuid4()
            callback.on_chain_start(None, {"input": "hello"}, run_id=call_id, name="test-agent")
            callback.on_chain_error(RuntimeError("password=private-value sk-private-token-123456 Authorization: Bearer opaque-private-credential"), run_id=call_id)
            run.finish(status="failed")

    asyncio.run(execute())
    exported = str([(span.attributes, span.status, span.events) for span in spans_after_flush(telemetry)])
    assert "private-value" not in exported and "sk-private-token" not in exported
    assert "opaque-private-credential" not in exported
    assert "ERROR" in exported


def test_content_opt_out_keeps_structure_and_usage(telemetry):
    tracing, _ = telemetry
    # Use a new project/client to install a distinct masking policy.
    settings = replace(tracing.settings, langfuse_public_key=f"pk-lf-test-{uuid4()}", langfuse_capture_content=False)
    private = Tracing(settings)
    try:
        with private.run(**{**run_args(), "input": "confidential question"}) as run:
            run.finish(output="confidential answer")
        private.client.flush()
        exported = str([span.attributes for span in telemetry[1].get_finished_spans()])
        assert "confidential" not in exported
    finally:
        asyncio.run(private.close())


def test_messages_are_copied_without_runtime_objects():
    message = ToolMessage(content='{"api_key": "private-value", "result": "safe"}', tool_call_id="call-1")
    exported = redact_data({"messages": [message], "runtime": object()})
    assert exported["messages"][0]["tool_call_id"] == "call-1"
    assert "safe" in str(exported) and "private-value" not in str(exported)
    assert message.content.endswith('"safe"}')


def test_chat_adapter_reports_terminal_output_and_closes_events(telemetry):
    tracing, _ = telemetry
    execution = SimpleNamespace(replay_message=None, user_id="u", tenant_id="t", conversation_id=uuid4(),
        run_id=str(uuid4()), content="question", resuming=False, project_id=None,
        model=SimpleNamespace(profile_id="custom:test"), request_id="r", assistant_message_id=uuid4())
    closed = []

    async def events():
        try:
            yield {"type": "text", "text": "answer"}
            yield {"type": "completed", "content": "answer"}
        finally:
            closed.append(True)

    async def execute():
        result = [event async for event in trace_chat_execution(SimpleNamespace(tracing=tracing), execution, events())]
        assert result[-1]["content"] == "answer"

    asyncio.run(execute())
    root = next(span for span in spans_after_flush(telemetry) if span.name == "melonclaw-chat-turn")
    assert root.attributes["langfuse.observation.output"] == "answer"
    assert closed == [True]


def test_actual_background_graph_traces_subagent_and_tool(telemetry, monkeypatch, tmp_path):
    from langchain_core.tools import tool
    from test_task_agent import Model, build

    from melonclaw.core.agent import AgentContext
    from melonclaw.core.task_policy import TaskRunControl
    from melonclaw.services.task_execution import execute_task_agent

    @tool
    async def internet_search(query: str) -> str:
        """Return a controlled source for tracing verification."""
        return "source result"

    monkeypatch.setattr("melonclaw.core.task_agent.build_task_search_tool", lambda key: internet_search)
    tracing, _ = telemetry
    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"name": "task", "args": {
            "subagent_type": "general-purpose", "description": "Search for a controlled source",
        }, "id": "delegate-1"}]),
        AIMessage(content="", tool_calls=[{"name": "internet_search", "args": {"query": "source"}, "id": "search-1"}]),
        AIMessage(content="child answer"),
        AIMessage(content="final answer"),
    ])

    async def execute():
        control = TaskRunControl(20, 20)
        settings = replace(tracing.settings, deepseek_api_key="fixture-key")
        agent, _, _ = await build(monkeypatch, tmp_path, model, settings=settings, control=control, search_authorized=True)
        with tracing.run(**{**run_args(), "feature": "task-run"}) as trace:
            outcome = await execute_task_agent(agent, run_id=uuid4(), instruction="Delegate research",
                context=AgentContext(user_id="test-user", tenant_id="test-tenant", tenant_name="test"),
                control=control, settings=settings)
            assert outcome.status == "succeeded", outcome
            trace.finish(output=outcome.result_markdown, status=outcome.status)

    asyncio.run(execute())
    spans = spans_after_flush(telemetry)
    root = next(span for span in spans if span.name == "melonclaw-task-run")
    child = next(span for span in spans if span.name == "general-purpose-agent")
    search = next(span for span in spans if span.name == "internet_search")
    assert child.attributes["langfuse.observation.type"] == "agent"
    assert search.attributes["langfuse.observation.type"] == "tool"
    assert all(span.context.trace_id == root.context.trace_id for span in spans)
    by_id = {span.context.span_id: span for span in spans}
    ancestor = search
    while ancestor.parent and ancestor.parent.span_id != child.context.span_id:
        ancestor = by_id[ancestor.parent.span_id]
    assert ancestor.parent and ancestor.parent.span_id == child.context.span_id
    assert sum(span.attributes.get("langfuse.observation.type") == "generation" for span in spans) == 4
    assert not any(span.attributes.get("langfuse.observation.type") == "agent"
                   and "Middleware" in span.name for span in spans)


def test_agent_shell_cannot_inherit_langfuse_credentials_even_with_allowlist():
    from melonclaw.core.defaults import resolve_agent_env

    env = resolve_agent_env(environ={"LANGFUSE_SECRET_KEY": "private-value", "LANGFUSE_PUBLIC_KEY": "public-value",
                           "MELONCLAW_SHELL_ENV_ALLOWLIST": "LANGFUSE_*"})
    assert "LANGFUSE_SECRET_KEY" not in env and "LANGFUSE_PUBLIC_KEY" not in env


def test_cancelled_chat_closes_inner_stream_and_resets_trace(telemetry):
    from contextlib import aclosing

    tracing, _ = telemetry
    execution = SimpleNamespace(replay_message=None, user_id="u", tenant_id="t", conversation_id=uuid4(),
        run_id=str(uuid4()), content="question", resuming=False, project_id=None,
        model=SimpleNamespace(profile_id="custom:test"), request_id="r", assistant_message_id=uuid4())
    closed = []

    async def events():
        try:
            yield {"type": "text", "text": "partial"}
            await asyncio.Future()
        finally:
            closed.append(True)

    async def execute():
        async with aclosing(trace_chat_execution(SimpleNamespace(tracing=tracing), execution, events())) as stream:
            await anext(stream)
        assert not trace_config().get("callbacks")

    asyncio.run(execute())
    root = next(span for span in spans_after_flush(telemetry) if span.name == "melonclaw-chat-turn")
    assert root.attributes["langfuse.observation.status_message"] == "cancelled"
    assert closed == [True]
