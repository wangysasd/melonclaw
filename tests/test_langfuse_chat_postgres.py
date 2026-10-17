"""PostgreSQL-backed chat execution with offline models and in-memory Langfuse export."""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from test_langfuse_tracing import spans_after_flush
from test_langfuse_tracing import telemetry as telemetry  # noqa: F401
from test_task_agent import Model, resolved_model, settings_for
from test_task_postgres import database_fixture

from melonclaw.core import agent as agent_module
from melonclaw.memory import MemoryService
from melonclaw.services.chat import ChatService
from melonclaw.services.runtime import ChatRuntime

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)


@asynccontextmanager
async def chat_runtime(storage, tmp_path, tracing):
    """Build a real Deep Agent with PostgreSQL checkpoint and Memory stores."""
    from melonclaw.database.connection import derive_psycopg_database_url

    async with storage.engine.connect() as conn:
        schema = await conn.scalar(text("select current_schema()"))
    pool = AsyncConnectionPool(
        derive_psycopg_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={schema}",
        },
    )
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        memory_store = AsyncPostgresStore(conn=pool)
        await memory_store.setup()
        settings = settings_for(tmp_path)
        runtime = ChatRuntime(
            settings=settings,
            storage=storage,
            checkpointer=checkpointer,
            memory_store=memory_store,
            memory_service=MemoryService(storage, memory_store),
        )
        runtime.tracing = tracing

        async def resolve_model(user_id, model_id=None):
            return resolved_model()

        runtime.resolve_model = resolve_model
        yield ChatService(runtime)
    finally:
        await pool.close()


def install_offline_model(monkeypatch, responses):
    """Return canned model replies without reaching a provider API."""
    model = Model(responses=[AIMessage(content=text) for text in responses])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: model)
    return model


async def send(chat, conversation_id, content, request_id=None):
    prepared = await chat.prepare_message(
        UUID(str(conversation_id)),
        "admin",
        request_id or str(uuid4()),
        content,
        model_id="custom:offline",
    )
    return [event async for event in chat.stream_execution(prepared)]


def test_chat_turns_share_session_but_have_distinct_traces_and_replay_is_idempotent(
    monkeypatch, tmp_path, telemetry
):
    tracing, _ = telemetry
    model = install_offline_model(monkeypatch, ["第一轮回答", "第二轮回答"])

    async def run():
        async with database_fixture() as storage:
            async with chat_runtime(storage, tmp_path, tracing) as chat:
                conversation = await chat.create_conversation("admin")
                conversation_id = UUID(conversation["id"])
                request_id = str(uuid4())

                first_events = await send(chat, conversation_id, "第一个问题", request_id)
                second_events = await send(chat, conversation_id, "第二个问题")
                replay_events = await send(chat, conversation_id, "第一个问题", request_id)

                assert first_events[-1]["type"] == "done"
                assert second_events[-1]["type"] == "done"
                assert any(
                    e.get("type") == "completed" and e.get("content") == "第一轮回答"
                    for e in first_events
                )
                assert any(
                    e.get("type") == "completed" and e.get("content") == "第二轮回答"
                    for e in second_events
                )
                assert any(e.get("replayed") for e in replay_events)
                assert model.i == 0  # Two turns consumed two replies; the replay consumed none.

                _, rows, _ = await storage.list_messages(
                    conversation_id, "admin", limit=20, before_seq=None
                )
                assistant_rows = [row for row in rows if row["role"] == "assistant"]
                assert [row["content"] for row in assistant_rows] == ["第一轮回答", "第二轮回答"]
                assert all(row["status"] == "completed" for row in assistant_rows)

    asyncio.run(run())
    roots = [span for span in spans_after_flush(telemetry) if span.name == "melonclaw-chat-turn"]
    assert len(roots) == 2
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert roots[0].attributes["session.id"] == roots[1].attributes["session.id"]
    assert roots[0].attributes["session.id"].endswith(":") is False


def test_chat_persists_terminal_stream_content_when_tracing_is_disabled(
    monkeypatch, tmp_path, telemetry
):
    tracing, _ = telemetry
    install_offline_model(monkeypatch, ["已保存的回答"])

    async def run():
        async with database_fixture() as storage:
            async with chat_runtime(storage, tmp_path, tracing) as chat:
                chat.runtime.tracing = None
                conversation = await chat.create_conversation("admin")
                events = await send(chat, conversation["id"], "离线问题")
                completed = next(event for event in events if event.get("type") == "completed")
                assert completed["content"] == "已保存的回答"
                assert events[-1]["type"] == "done"
                _, rows, _ = await storage.list_messages(
                    UUID(conversation["id"]), "admin", limit=10, before_seq=None
                )
                assistant = next(row for row in rows if row["role"] == "assistant")
                assert assistant["status"] == "completed"
                assert assistant["content"] == completed["content"]

    asyncio.run(run())
    assert not [span for span in spans_after_flush(telemetry) if span.name == "melonclaw-chat-turn"]
