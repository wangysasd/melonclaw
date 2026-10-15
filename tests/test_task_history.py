"""任务累计历史、请求投影、原文工具和有界摘要。"""

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy import update
from test_task_agent import Model, settings_for
from test_task_postgres import claim, database_fixture, enqueue, install_model

from melonclaw.core import task_agent
from melonclaw.core.agent import AgentContext
from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_history import history_messages, merge_text_messages
from melonclaw.database.schema import users
from melonclaw.middleware.task_context import TaskContextMiddleware
from melonclaw.repository.task_history import capture_history, load_history
from melonclaw.services.task_history import RunHistoryProvider, prepare_history, read_snapshot
from melonclaw.services.task_scheduler import TaskScheduler
from melonclaw.services.tasks import TaskService

pg = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")


class RecordingModel(Model):
    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_merge_only_adjacent_plain_text_without_mutating_tool_protocol():
    first, second = AIMessage(content="第一集"), AIMessage(content="第二集")
    tool_call = AIMessage(content="", tool_calls=[{"name": "read_task_history", "args": {}, "id": "call"}])
    tool_result = ToolMessage(content="原文", tool_call_id="call")
    messages = [HumanMessage(content="任务"), first, second, HumanMessage(content="追问"), tool_call, tool_result, AIMessage(content="回答")]
    merged = merge_text_messages(messages)
    assert len(merged) == 6
    assert merged[1].content == "第一集\n\n第二集"
    assert first.content == "第一集" and second.content == "第二集"
    assert merged[3] is tool_call and merged[4] is tool_result
    reasoning = AIMessage(content="有推理协议", additional_kwargs={"reasoning_content": "内部字段"})
    assert len(merge_text_messages([first, reasoning])) == 2


@pg
def test_three_runs_continue_from_history_and_publish_only_assistants(monkeypatch, tmp_path):
    install_model(monkeypatch)
    model = RecordingModel(responses=[AIMessage(content="第1集：林舟找到了钥匙"), AIMessage(content="第2集：林舟用钥匙打开暗门"), AIMessage(content="第3集：林舟进入暗门后的城市")])
    monkeypatch.setattr(task_agent, "build_chat_model", lambda *a, **k: model)

    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service, instruction="写一部30集小说，每次继续写一集，主角林舟。")
            cid, tid = UUID(saved["task"]["conversation_id"]), UUID(saved["task"]["id"])
            for index in range(3):
                if index:
                    await executor.service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
                await executor.execute(await claim(storage.task_queue))
            _, rows, _ = await storage.list_messages(cid, "admin", limit=100, before_seq=None)
            assert [row["role"] for row in rows] == ["user", "assistant", "assistant", "assistant"]
            assert all(row["status"] == "completed" for row in rows)
            assert "创建任务" in rows[0]["content"]
            assert "第1集" in str(model.seen[2]) and "第2集" in str(model.seen[2])
            assert "已成功交付 2 次" in str(model.seen[2])
            assistant_history = [m for m in model.seen[2] if isinstance(m, AIMessage)]
            assert len(assistant_history) == 1
            assert "第1集" in assistant_history[0].content and "第2集" in assistant_history[0].content
            assert await storage.find_request(cid, "admin", rows[-1]["request_id"]) is None
            snapshot = (await executor.service.get_run("admin", UUID(saved["run"]["id"])))["config_snapshot"]["history"]
            assert snapshot["completed_runs"] == 0

    asyncio.run(run())


@pg
def test_history_is_frozen_scoped_and_original_text_remains_readable(monkeypatch, tmp_path):
    install_model(monkeypatch)

    async def run():
        async with database_fixture() as storage:
            service = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver()).service
            saved = await enqueue(service)
            cid, tid = UUID(saved["task"]["conversation_id"]), UUID(saved["task"]["id"])
            await storage.task_queue.finish(await claim(storage.task_queue), status="succeeded", result="第一集原文")
            async with storage.engine.connect() as conn:
                snapshot = await capture_history(conn, "admin", cid)
                rows = await load_history(conn, "admin", snapshot)
            await service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            await storage.task_queue.finish(await claim(storage.task_queue), status="failed", result="未完成第二集", error_code="injected")
            value = await read_snapshot(storage.engine, "admin", snapshot, start_seq=2, max_chars=8000)
            assert "第一集原文" in value["text"] and "未完成第二集" not in value["text"]
            assert value["completed_runs"] == 1
            async with storage.engine.connect() as conn:
                current = await capture_history(conn, "admin", cid)
                current_rows = await load_history(conn, "admin", current)
            assert current["completed_runs"] == 1
            assert "未完成第二集" in str(history_messages(current_rows, current))
            assert "不计完成进度" in str(history_messages(current_rows, current))
            provider = RunHistoryProvider(storage.engine, "admin", "system", snapshot, rows)
            with pytest.raises(TaskError):
                await provider.read_history(AgentContext(user_id="bob", tenant_id="system", tenant_name="系统"))
            async with storage.engine.begin() as conn:
                await conn.execute(update(users).where(users.c.user_id == "admin").values(tenant_id="dep-a"))
            with pytest.raises(TaskError):
                await read_snapshot(storage.engine, "admin", snapshot)

    asyncio.run(run())


def test_long_history_is_summarized_without_deleting_recent_or_original(monkeypatch, tmp_path):
    model = RecordingModel(responses=[AIMessage(content="前文摘要：主角林舟得到钥匙，尚未打开暗门。来源消息 #1。")])
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: model)
    now = datetime.now(UTC)
    rows = [{"id": uuid4(), "seq": index + 1, "role": "assistant", "content": "旧剧情钥匙" * 700 if index == 0 else "最近一集：来到暗门。", "status": "completed", "display_metadata": {}, "created_at": now} for index in range(2)]
    snapshot = {"sources": [], "completed_runs": 2, "summary": "", "summary_until": 0}

    async def run():
        result = await prepare_history(snapshot, rows, SimpleNamespace(context_window=8192), settings_for(tmp_path, agent_output_reserve=512, agent_summary_keep_tokens=512, task_history_summary_max_calls=32, agent_model_call_limit=64))
        assert result["summary_until"] == 1 and "林舟" in result["summary"]
        messages = history_messages(rows, result)
        assert "最近一集" in str(messages)
        assert rows[0]["content"] == "旧剧情钥匙" * 700
        assert snapshot["summary"] == ""
        assert model.seen

    asyncio.run(run())


@pg
def test_completed_finite_goal_pauses_only_after_successful_delivery(monkeypatch, tmp_path):
    install_model(monkeypatch)
    model = RecordingModel(responses=[AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {}, "id": "done"}]), AIMessage(content="最后一集：故事完结。")])
    monkeypatch.setattr(task_agent, "build_chat_model", lambda *a, **k: model)

    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service, instruction="本小说仅一集，这次写完即完成全部目标。")
            await executor.execute(await claim(storage.task_queue))
            task = await executor.service.repository.get_task("admin", UUID(saved["task"]["id"]))
            assert task["status"] == "paused" and task["pause_reason"] == "goal_completed"
            assert task["next_run_at"] is None
            report = await executor.service.get_run("admin", UUID(saved["run"]["id"]))
            assert report["status"] == "succeeded" and "故事完结" in report["result_markdown"]
            assert model.seen[-1][-1].type == "tool"

    asyncio.run(run())


@pg
def test_summary_is_reused_when_prior_sources_are_unchanged(monkeypatch, tmp_path):
    install_model(monkeypatch)
    summary_model = RecordingModel(responses=[AIMessage(content="摘要：林舟得到了钥匙。未解伏笔是暗门，来源消息 #2。")])
    monkeypatch.setattr("melonclaw.services.task_history.build_chat_model", lambda *a, **k: summary_model)

    async def run():
        async with database_fixture() as storage:
            service = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver()).service
            saved = await enqueue(service)
            await storage.task_queue.finish(await claim(storage.task_queue), status="succeeded", result="林舟得到钥匙" * 700)
            tid = UUID(saved["task"]["id"])
            await service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            second = await claim(storage.task_queue)
            async with storage.engine.connect() as conn:
                rows = await load_history(conn, "admin", second["config_snapshot"]["history"])
            settings = settings_for(tmp_path, agent_output_reserve=512, agent_summary_keep_tokens=512, task_history_summary_max_calls=32, agent_model_call_limit=64)
            model = SimpleNamespace(context_window=8192)
            snapshot = await prepare_history(second["config_snapshot"]["history"], rows, model, settings)
            await storage.task_queue.save_history(second, snapshot)
            await storage.task_queue.finish(second, status="succeeded", result="林舟来到暗门")
            calls = len(summary_model.seen)
            third = await service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            assert third["config_snapshot"]["history"]["summary"] == snapshot["summary"]
            async with storage.engine.connect() as conn:
                rows = await load_history(conn, "admin", third["config_snapshot"]["history"])
            again = await prepare_history(third["config_snapshot"]["history"], rows, model, settings)
            assert len(summary_model.seen) == calls
            assert "暗门" in str(history_messages(rows, again))

    asyncio.run(run())



def test_request_projection_keeps_current_skill_and_tool_chain():
    user = HumanMessage(content="解释第二集", id="current")
    call = AIMessage(content="", tool_calls=[{"name": "read_task_history", "args": {}, "id": "read"}])
    result = ToolMessage(content="第二集原文", tool_call_id="read")
    selected_skill = SystemMessage(content="本轮选定的写作技能")
    old_tool = AIMessage(content="", tool_calls=[{"name": "old_tool", "args": {}, "id": "old"}])
    original = [HumanMessage(content="旧问题", id="old"), old_tool, selected_skill, user, call, result]

    async def run():
        async def hydrate(*args):
            return {"history_messages": [HumanMessage(content="创作要求"), AIMessage(content="第一集"), AIMessage(content="第二集")], "completed_runs": 2}
        request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(user_message_id="current")), messages=original)
        request.override = lambda **kwargs: SimpleNamespace(**kwargs)
        captured = []
        async def handler(value):
            captured.extend(value.messages)
        await TaskContextMiddleware(SimpleNamespace(hydrate=hydrate)).awrap_model_call(request, handler)
        assert selected_skill in captured
        assert captured[-3:] == [user, call, result]
        assert old_tool not in captured
        assert len([message for message in captured if isinstance(message, AIMessage) and not message.tool_calls]) == 1
        assert original[1] is old_tool and original[-1] is result

    asyncio.run(run())


@pg
def test_completion_cannot_pause_updated_goal_or_failed_run(monkeypatch, tmp_path):
    install_model(monkeypatch)
    async def run():
        async with database_fixture() as storage:
            service = TaskService(settings_for(tmp_path), storage)
            saved = await enqueue(service)
            owned = await claim(storage.task_queue)
            await storage.task_queue.finish(owned, status="failed", result="部分", goal_complete=True)
            tid = UUID(saved["task"]["id"])
            assert (await service.repository.get_task("admin", tid))["status"] == "enabled"
            await service.run("admin", tid, expected_version=1, idempotency_key=str(uuid4()))
            owned = await claim(storage.task_queue)
            current = await service.repository.get_task("admin", tid)
            await service.save("admin", task_id=tid, expected_version=1, definition=current["definition"] | {"instruction": "新目标，还需要继续写"}, enabled=True, idempotency_key=str(uuid4()))
            await storage.task_queue.finish(owned, status="succeeded", result="旧目标完成", goal_complete=True)
            task = await service.repository.get_task("admin", tid)
            assert task["status"] == "enabled" and task["version"] == 2
    asyncio.run(run())


@pg
def test_real_agent_reads_bound_original_with_injected_runtime(monkeypatch, tmp_path):
    install_model(monkeypatch)
    model = RecordingModel(responses=[
        AIMessage(content="第一集：林舟得到钥匙。"),
        AIMessage(content="", tool_calls=[{"name": "read_task_history", "args": {"start_seq": 2, "max_chars": 1000}, "id": "history"}]),
        AIMessage(content="第二集：林舟继续寻找暗门。"),
    ])
    monkeypatch.setattr(task_agent, "build_chat_model", lambda *a, **k: model)
    async def run():
        async with database_fixture() as storage:
            executor = TaskScheduler(settings_for(tmp_path), storage, InMemorySaver())
            executor.queue = storage.task_queue
            saved = await enqueue(executor.service)
            await executor.execute(await claim(storage.task_queue))
            await executor.service.run("admin", UUID(saved["task"]["id"]), expected_version=1, idempotency_key=str(uuid4()))
            await executor.execute(await claim(storage.task_queue))
            returns = [message for message in model.seen[-1] if isinstance(message, ToolMessage)]
            assert returns and "第一集：林舟得到钥匙" in returns[-1].content
            assert returns[-1].status == "success"
    asyncio.run(run())
