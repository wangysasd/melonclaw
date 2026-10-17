"""跨轮恢复、并发、SDK 生命周期与真实任务调度的监控回归。"""

import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import langfuse
import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_langfuse_tracing import run_args, spans_after_flush
from test_langfuse_tracing import telemetry as telemetry  # noqa: F401
from test_task_agent import Model, settings_for
from test_task_postgres import claim, database_fixture, enqueue, install_model

from melonclaw.core.config import Settings
from melonclaw.core.task_errors import TaskError
from melonclaw.core.tracing import Tracing, trace_config
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tracing import trace_chat_execution


def execution(**overrides):
    values = dict(replay_message=None, user_id="u", tenant_id="t", conversation_id=uuid4(),
                  run_id=str(uuid4()), content="question", resuming=False, project_id=None,
                  model=SimpleNamespace(profile_id="custom:test"), request_id="r", assistant_message_id=uuid4())
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("feature", ["chat-turn", "task-run"])
def test_switch_off_runs_actual_model_without_client_or_callbacks(monkeypatch, tmp_path, feature):
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kw: pytest.fail("client initialized when disabled"))
    tracing = Tracing(Settings(workspace_root=tmp_path, langfuse_enabled=False))

    async def execute():
        with tracing.run(**(run_args() | {"feature": feature})):
            model = FakeMessagesListChatModel(responses=[AIMessage(content="working")])
            assert not trace_config().get("callbacks")
            assert (await model.ainvoke("hello", config=trace_config())).content == "working"
        await tracing.close()
        await tracing.close()

    asyncio.run(execute())


@pytest.mark.parametrize("url", ["", "broken", "https://[", "https://user:password@host.test", "https://host.test/?key=unsafe"])
def test_invalid_monitoring_address_does_not_block_business(monkeypatch, tmp_path, url):
    monkeypatch.setattr(langfuse, "Langfuse", lambda **kw: pytest.fail("invalid address used"))
    settings = Settings(workspace_root=tmp_path, langfuse_enabled=True, langfuse_public_key="test-public",
                        langfuse_secret_key="test-secret", langfuse_base_url=url)
    tracing = Tracing(settings)
    with tracing.run(**run_args()):
        assert not trace_config().get("callbacks")
    asyncio.run(tracing.close())


def test_replay_does_not_duplicate_trace_and_interruption_has_terminal_state(telemetry):
    tracing, _ = telemetry

    async def events(kind):
        yield {"type": kind, "content": "cached answer"}

    async def execute():
        replay = execution(replay_message={"content": "cached answer"})
        result = [event async for event in trace_chat_execution(SimpleNamespace(tracing=tracing), replay, events("completed"))]
        assert result == [{"type": "completed", "content": "cached answer"}]
        assert not spans_after_flush(telemetry)
        for kind in ("approval", "user_input_required"):
            current = execution()
            result = [event async for event in trace_chat_execution(SimpleNamespace(tracing=tracing), current, events(kind))]
            assert result[0]["type"] == kind
        assert not trace_config().get("callbacks")

    asyncio.run(execute())
    roots = spans_after_flush(telemetry)
    assert len(roots) == 2
    assert all(span.attributes["langfuse.observation.status_message"] == "interrupted" for span in roots)


def test_concurrent_model_calls_keep_tenant_session_and_parent_separate(telemetry):
    tracing, _ = telemetry
    records = []

    async def execute(index):
        tenant, user, conversation = f"tenant-{index % 4}", f"user-{index}", f"conversation-{index}"
        run_id = str(uuid4())
        args = run_args() | dict(tenant_id=tenant, user_id=user, conversation_id=conversation, run_id=run_id)
        config = {"configurable": {"thread_id": conversation}, "metadata": {"ls_model_name": "fixture-model"}}
        with tracing.run(**args) as trace:
            await asyncio.sleep(0)
            model = FakeMessagesListChatModel(responses=[AIMessage(content=f"answer-{index}")])
            result = await model.ainvoke([HumanMessage(content=f"question-{index}")], config=trace_config(config))
            trace.finish(output=result.content)
            records.append((tracing.client.create_trace_id(seed=run_id), f"{tenant}:{user}", f"{tenant}:{conversation}"))
        assert not trace_config().get("callbacks")
        assert "callbacks" not in config and config["metadata"] == {"ls_model_name": "fixture-model"}

    async def parallel():
        await asyncio.gather(*(execute(index) for index in range(20)))

    asyncio.run(parallel())
    spans = spans_after_flush(telemetry)
    assert len(spans) == 40
    expected = {int(trace_id, 16): (user, session) for trace_id, user, session in records}
    for span in spans:
        user, session = expected[span.context.trace_id]
        assert span.attributes["user.id"] == user
        assert span.attributes["session.id"] == session
    roots = {span.context.trace_id: span for span in spans if span.name == "melonclaw-chat-turn"}
    for span in spans:
        if span.attributes.get("langfuse.observation.type") == "generation":
            assert span.parent.span_id == roots[span.context.trace_id].context.span_id


def test_actual_approval_resume_stays_in_session_with_two_distinct_traces(telemetry):
    tracing, _ = telemetry
    calls = []

    @tool
    def approval_probe(value: str) -> str:
        """A controlled approval action that only appends to a test list."""
        calls.append(value)
        return "approved result"

    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"name": "approval_probe", "args": {"value": "test"}, "id": "approval-call"}]),
        AIMessage(content="finished"),
    ])
    agent = create_deep_agent(model=model, tools=[approval_probe], interrupt_on={"approval_probe": True},
                              checkpointer=InMemorySaver(), name="test-approval-agent")
    config = {"configurable": {"thread_id": str(uuid4())}, "metadata": {"ls_model_name": "fixture-model"}}
    args = run_args()

    async def execute():
        with tracing.run(**args) as trace:
            pending = await agent.ainvoke({"messages": [HumanMessage(content="run controlled approval")]}, trace_config(config))
            assert pending.get("__interrupt__") and calls == []
            trace.finish(status="interrupted")
        with tracing.run(**(args | {"run_id": str(uuid4()), "input": {"resuming": True}})) as trace:
            result = await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), trace_config(config))
            assert result["messages"][-1].content == "finished" and calls == ["test"]
            trace.finish(output="finished")
        assert not trace_config().get("callbacks")

    asyncio.run(execute())
    spans = spans_after_flush(telemetry)
    roots = [span for span in spans if span.name == "melonclaw-chat-turn"]
    assert len(roots) == 2 and len({span.context.trace_id for span in roots}) == 2
    assert len({span.attributes["session.id"] for span in spans}) == 1
    assert {span.attributes["langfuse.observation.status_message"] for span in roots} == {"completed", "interrupted"}


def test_root_update_and_teardown_failure_preserve_business_result(telemetry, monkeypatch):
    tracing, _ = telemetry
    real_start = tracing.client.start_as_current_observation

    @contextmanager
    def broken_teardown(**kwargs):
        with real_start(**kwargs) as span:
            monkeypatch.setattr(span, "update", lambda **kw: (_ for _ in ()).throw(RuntimeError("sdk update failure")))
            yield span
        raise RuntimeError("sdk teardown failure")

    monkeypatch.setattr(tracing.client, "start_as_current_observation", broken_teardown)
    with tracing.run(**run_args()) as trace:
        trace.finish(output="business answer")
    with pytest.raises(ValueError, match="original business failure"):
        with tracing.run(**run_args()):
            raise ValueError("original business failure")
    assert not trace_config().get("callbacks")


pg = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")


@pg
@pytest.mark.parametrize("scenario", ["success", "build_failure", "build_timeout", "execute_timeout", "service_shutdown"])
def test_scheduler_trace_terminal_matches_committed_task(telemetry, monkeypatch, tmp_path, scenario):
    tracing, _ = telemetry
    install_model(monkeypatch)
    monkeypatch.setattr("melonclaw.core.task_agent.build_chat_model", lambda *a, **k: Model(responses=[AIMessage(content="task answer")]))
    original_build = __import__("melonclaw.services.task_scheduler", fromlist=["build_research_agent"]).build_research_agent
    entered = asyncio.Event()

    class HangingAgent:
        async def astream(self, *args, **kwargs):
            entered.set()
            await asyncio.Future()
            yield None

    async def build(*args, **kwargs):
        if scenario == "build_failure":
            raise TaskError("test_failure", "controlled build failure")
        if scenario in {"build_timeout", "service_shutdown"}:
            entered.set()
            await asyncio.Future()
        if scenario == "execute_timeout":
            return HangingAgent()
        return await original_build(*args, **kwargs)

    monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", build)

    async def execute():
        async with database_fixture() as storage:
            settings = settings_for(tmp_path, task_execution_timeout_seconds=1, task_poll_seconds=1)
            executor = TaskScheduler(settings, storage, InMemorySaver(), tracing=tracing)
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            owned = await claim(executor.queue)
            job = asyncio.create_task(executor.execute(owned))
            if scenario == "service_shutdown":
                await asyncio.wait_for(entered.wait(), 2)
                job.cancel()
            await asyncio.wait_for(job, 4)
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            spans = spans_after_flush(telemetry)
            root = next(span for span in spans if span.name == "melonclaw-task-run")
            assert root.attributes["langfuse.observation.status_message"] == result["status"]
            assert root.attributes["session.id"] == f"system:{saved['task']['conversation_id']}"
            assert result["status"] == ("succeeded" if scenario == "success" else "failed")
            if scenario.endswith("timeout"):
                assert result["error_code"] == "execution_timeout"
            if scenario == "service_shutdown":
                assert result["error_code"] == "service_shutdown"

    asyncio.run(execute())


@pytest.mark.parametrize("name", ["langfuse.internal", "opentelemetry.exporter.otlp.proto.http.trace_exporter.internal", "opentelemetry.sdk._shared_internal.batch"])
def test_descendant_sdk_logs_are_sanitized_before_any_handler(telemetry, caplog, name):
    import logging

    private = "synthetic-private-log-secret"
    caplog.set_level(logging.WARNING)
    logging.getLogger(name).warning("body=%s", private, exc_info=RuntimeError(private))
    assert private not in caplog.text
    assert "Langfuse telemetry diagnostic" in caplog.text


@pg
def test_user_cancel_while_running_is_cancelled_in_database_and_trace(telemetry, monkeypatch, tmp_path):
    tracing, _ = telemetry
    install_model(monkeypatch)
    entered, stopped = asyncio.Event(), asyncio.Event()

    class HangingAgent:
        async def astream(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
                yield None
            finally:
                stopped.set()

    async def build(*args, **kwargs):
        return HangingAgent()

    monkeypatch.setattr("melonclaw.services.task_scheduler.build_research_agent", build)

    async def execute():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path, task_poll_seconds=1), storage, InMemorySaver(), tracing=tracing)
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            owned = await claim(executor.queue)
            job = asyncio.create_task(executor.execute(owned))
            await asyncio.wait_for(entered.wait(), 2)
            await executor.service.cancel("admin", UUID(saved["run"]["id"]), idempotency_key=str(uuid4()))
            await asyncio.wait_for(job, 4)
            result = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            root = next(span for span in spans_after_flush(telemetry) if span.name == "melonclaw-task-run")
            assert stopped.is_set() and result["status"] == "cancelled"
            assert root.attributes["langfuse.observation.status_message"] == "cancelled"
            assert root.attributes["langfuse.observation.metadata.error_code"] == '"cancelled"'

    asyncio.run(execute())


@pg
def test_scheduler_history_summary_and_agent_are_inside_one_trace(telemetry, monkeypatch, tmp_path):
    from test_task_agent import resolved_model

    tracing, _ = telemetry
    settings = settings_for(tmp_path, agent_output_reserve=512, agent_summary_keep_tokens=512,
                            task_history_summary_max_calls=32, agent_model_call_limit=64)

    async def resolve(*args, **kwargs):
        return replace(resolved_model(), context_window=8192)

    monkeypatch.setattr("melonclaw.services.tasks.TaskService.resolve_model", resolve)
    summary = FakeMessagesListChatModel(responses=[AIMessage(content="summary of prior work")])
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: summary)
    monkeypatch.setattr("melonclaw.core.task_agent.build_chat_model", lambda *a, **k: Model(responses=[AIMessage(content="continued result")]))

    async def execute():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings, storage, InMemorySaver(), tracing=tracing)
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            await executor.queue.finish(await claim(executor.queue), status="succeeded", result="prior research " * 700)
            await executor.service.run("admin", UUID(saved["task"]["id"]), expected_version=1, idempotency_key=str(uuid4()))
            owned = await claim(executor.queue)
            await asyncio.wait_for(executor.execute(owned), 8)
            result = await executor.service.get_run("admin", owned["id"])
            assert result["status"] == "succeeded", result["error_code"]
            assert result["config_snapshot"]["history"]["summary"] == "summary of prior work"
            spans = spans_after_flush(telemetry)
            root = next(span for span in spans if span.name == "melonclaw-task-run")
            assert {span.context.trace_id for span in spans} == {root.context.trace_id}
            assert any(span.name == "task-history-summary" for span in spans)
            assert sum(span.attributes.get("langfuse.observation.type") == "generation" for span in spans) > 1

    asyncio.run(execute())


@pg
def test_actual_apscheduler_trigger_uses_shared_tracing_client(telemetry, monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update
    from test_task_postgres import definition

    from melonclaw.database.schema import tasks

    tracing, _ = telemetry
    install_model(monkeypatch)
    monkeypatch.setattr("melonclaw.core.task_agent.build_chat_model", lambda *a, **k: Model(responses=[AIMessage(content="scheduled answer")]))

    async def execute():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path, task_poll_seconds=1), storage, InMemorySaver(), tracing=tracing)
            when = (datetime.now(UTC) + timedelta(hours=1)).replace(second=0, microsecond=0)
            saved = await executor.service.save("admin", definition=definition(schedule={"kind": "once", "run_at": when.isoformat()}),
                                                enabled=True, idempotency_key=str(uuid4()))
            tid = UUID(saved["task"]["id"])
            async with storage.engine.begin() as conn:
                await conn.execute(update(tasks).where(tasks.c.id == tid).values(next_run_at=datetime.now(UTC)))
            try:
                await executor.start()
                async with asyncio.timeout(8):
                    while True:
                        history = await executor.service.repository.runs("admin", tid)
                        if history["items"] and history["items"][0]["status"] == "succeeded":
                            break
                        await asyncio.sleep(0.05)
                assert history["total"] == 1 and history["items"][0]["trigger_type"] == "schedule"
                root = next(span for span in spans_after_flush(telemetry) if span.name == "melonclaw-task-run")
                assert root.attributes["langfuse.observation.status_message"] == "succeeded"
                assert root.attributes["langfuse.trace.metadata.task_id"] == json.dumps(str(tid))
            finally:
                await executor.close()

    asyncio.run(execute())
