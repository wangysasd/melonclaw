"""根 Agent assistant steps 的顺序、关联和终态测试。"""

import asyncio

import pytest

from melonclaw.output import assistant_steps as steps_module
from melonclaw.output.assistant_steps import MAX_STEPS_BYTES, AssistantStepAccumulator
from melonclaw.output.events import iter_research_events


def test_event_projection_updates_the_execution_snapshot_once():
    async def empty():
        if False:
            yield None

    class Message:
        id = "ai-1"
        metadata = {}
        output = {"content": "你好", "tool_calls": []}

        def __aiter__(self):
            async def chunks():
                yield {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "你好"}}

            return chunks()

    class Stream:
        tool_calls = empty()
        subagents = empty()
        extensions = {"custom": empty()}

        @property
        def messages(self):
            async def messages():
                yield Message()

            return messages()

        async def abort(self):
            return None

    class Agent:
        def astream_events(self, agent_input, **kwargs):
            return Stream()

    async def collect():
        projector = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
        events = [
            event
            async for event in iter_research_events(
                Agent(), {"messages": []}, {}, projector=projector
            )
        ]
        return projector, events

    projector, events = asyncio.run(collect())
    assert projector.steps[0]["content"] == "你好"
    assert [event["type"] for event in events if event["type"].startswith("assistant_")] == [
        "assistant_step_started",
        "assistant_text_delta",
        "assistant_step_completed",
    ]


def test_text_deltas_avoid_full_snapshot_scans_until_limit():
    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    step_id = accumulator.start_step()["step"]["id"]
    scans = 0
    original = accumulator._enforce_total_limit

    def count_scan():
        nonlocal scans
        scans += 1
        original()

    accumulator._enforce_total_limit = count_scan
    for _ in range(100):
        accumulator.project_text_delta(step_id, "普通文本")

    assert scans == 0
    assert accumulator.steps[0]["content"] == "普通文本" * 100


def test_many_steps_still_respect_total_snapshot_limit():
    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    for _ in range(6):
        step_id = accumulator.start_step()["step"]["id"]
        accumulator.project_text_delta(step_id, "字" * 100_000)

    assert len(str(accumulator.steps).encode("utf-8")) <= MAX_STEPS_BYTES
    assert any(step.get("truncated") for step in accumulator.steps)


@pytest.mark.parametrize("tail", ["", " \n\t", [{"type": "reasoning", "reasoning": "继续思考"}]])
def test_empty_or_reasoning_tail_does_not_hide_the_previous_answer(tail):
    accumulator = AssistantStepAccumulator(message_id="answer", run_id="run")
    answer_id = accumulator.start_step()["step"]["id"]
    accumulator.complete_step(answer_id, content="实际答案")
    tail_id = accumulator.start_step()["step"]["id"]
    accumulator.complete_step(tail_id, content=tail)

    assert accumulator.visible_content() == "实际答案"
    snapshot = accumulator.terminal_snapshot(status="completed", final_content=accumulator.visible_content())
    assert snapshot[0]["is_final"] is True
    assert snapshot[1]["is_final"] is False
    assert snapshot[0]["content"] == "实际答案"
    if isinstance(tail, list):
        assert snapshot[1]["content_blocks"] == [{"type": "reasoning", "text": "继续思考"}]


def test_answer_fallback_excludes_tool_steps_and_reasoning():
    accumulator = AssistantStepAccumulator(message_id="answer", run_id="run")
    step_id = accumulator.start_step()["step"]["id"]
    accumulator.complete_step(step_id, content="将要操作", tool_calls=[{"id": "call", "name": "read_file"}])
    tail = accumulator.start_step()["step"]["id"]
    accumulator.complete_step(tail, content=[{"type": "reasoning", "reasoning": "思考"}])
    assert accumulator.visible_content() == ""


@pytest.mark.parametrize("kind", ["text", "reasoning"])
def test_short_step_truncation_keeps_text_and_a_single_visible_marker(monkeypatch, kind):
    accumulator = AssistantStepAccumulator(message_id="answer", run_id="run")
    step_id = accumulator.start_step()["step"]["id"]
    accumulator.project_text_delta(step_id, "字" * 200, kind=kind)
    if kind == "text":
        accumulator.steps[0]["content_blocks"] = [{"type": "text", "text": "字" * 200}]
    limit = len(str(accumulator.steps).encode("utf-8")) - 50
    monkeypatch.setattr(steps_module, "MAX_STEPS_BYTES", limit)

    accumulator._enforce_total_limit()
    step = accumulator.steps[0]
    assert step["content"].startswith("字")
    assert step["content"].endswith(steps_module.TRUNCATION_MARKER)
    assert "".join(block["text"] for block in step["content_blocks"]) == step["content"]
    assert step["content_blocks"][-1]["type"] == kind
    assert len(str(accumulator.steps).encode("utf-8")) <= limit
    previous = step["content"]
    accumulator._enforce_total_limit()
    assert step["content"] == previous
    assert step["content"].count(steps_module.TRUNCATION_MARKER) == 1


def test_truncation_terminates_when_metadata_alone_exceeds_the_budget(monkeypatch):
    accumulator = AssistantStepAccumulator(message_id="answer", run_id="run")
    step_id = accumulator.start_step()["step"]["id"]
    accumulator.project_text_delta(step_id, "短答案")
    monkeypatch.setattr(steps_module, "MAX_STEPS_BYTES", 1)
    accumulator._enforce_total_limit()
    assert accumulator.steps[0]["content"] == steps_module.TRUNCATION_MARKER
    assert accumulator.steps[0]["truncated"] is True
    assert accumulator.visible_content() == ""


def test_truncation_uses_the_ordered_blocks_after_final_answer_reconciliation(monkeypatch):
    accumulator = AssistantStepAccumulator(message_id="answer", run_id="run")
    step_id = accumulator.start_step()["step"]["id"]
    accumulator.complete_step(step_id, content=[
        {"type": "reasoning", "reasoning": "分析" * 100},
        {"type": "text", "text": "答复" * 100},
    ])
    accumulator.terminal_snapshot(status="completed", final_content="答复" * 100)
    monkeypatch.setattr(steps_module, "MAX_STEPS_BYTES", len(str(accumulator.steps).encode("utf-8")) - 50)
    accumulator._enforce_total_limit()
    step = accumulator.steps[0]
    assert step["content"].startswith("分析")
    assert step["content"] == "".join(block["text"] for block in step["content_blocks"])
    assert step["content"].endswith(steps_module.TRUNCATION_MARKER)


def test_multiple_model_steps_keep_text_and_tools_in_causal_order():
    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    first = accumulator.start_step(source_message_id="ai-1")
    first_id = first["step"]["id"]
    assert accumulator.project_text_delta(first_id, "<think>hidden</think>文字 A") ["delta"] == "<think>hidden</think>文字 A"
    accumulator.complete_step(
        first_id,
        tool_calls=[
            {"id": "call-1", "name": "search_web", "args": {"q": "a"}},
            {"id": "call-2", "name": "execute", "args": {"command": "b"}},
        ],
    )
    accumulator.project_tool_result(call_id="call-2", result="结果 2")
    accumulator.project_tool_result(call_id="call-1", result="结果 1")

    second = accumulator.start_step(source_message_id="ai-2")
    second_id = second["step"]["id"]
    accumulator.project_text_delta(second_id, "文字 B")
    accumulator.complete_step(second_id)
    snapshot = accumulator.terminal_snapshot(status="completed", final_content="文字 B")

    assert [step["ordinal"] for step in snapshot] == [0, 1]
    assert [step["content"] for step in snapshot] == ["<think>hidden</think>文字 A", "文字 B"]
    assert [tool["call_id"] for tool in snapshot[0]["tool_calls"]] == ["call-1", "call-2"]
    assert [tool["result_preview"] for tool in snapshot[0]["tool_calls"]] == ["结果 1", "结果 2"]
    assert snapshot[1]["is_final"] is True
    assert snapshot[0]["is_final"] is False


def test_tool_events_can_arrive_before_the_complete_ai_message():
    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    assert accumulator.project_tool_call(
        call_id="call-1",
        name="search_web",
        args={"q": "redacted"},
    ) is None
    assert accumulator.project_tool_result(
        call_id="call-1",
        result="ok",
    ) is None

    started = accumulator.start_step()
    accumulator.complete_step(started["step"]["id"])
    step = accumulator.steps[0]
    assert step["tool_calls"][0]["call_id"] == "call-1"
    assert step["tool_calls"][0]["status"] == "completed"
    assert step["tool_calls"][0]["result_preview"] == "ok"


def test_interrupted_and_failed_runs_keep_visible_steps_without_marking_success():
    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    started = accumulator.start_step()
    step_id = started["step"]["id"]
    accumulator.project_text_delta(step_id, "已完成前置工作")
    accumulator.complete_step(
        step_id,
        tool_calls=[{"id": "call-1", "name": "execute", "args": {"command": "x"}}],
    )
    waiting = accumulator.terminal_snapshot(status="interrupted")
    assert waiting[0]["content"] == "已完成前置工作"
    assert waiting[0]["status"] == "waiting"
    assert waiting[0]["tool_calls"][0]["status"] == "waiting"
    assert waiting[0]["is_final"] is False

    failed = accumulator.terminal_snapshot(status="failed")
    assert failed[0]["content"] == "已完成前置工作"
    assert failed[0]["tool_calls"][0]["status"] == "unknown"
    assert failed[0]["is_final"] is False


def test_resumed_run_no_longer_sticks_on_waiting_step():
    """HITL 恢复后工具结果要同步 step 状态，终态也要收敛 waiting。"""

    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    started = accumulator.start_step()
    step_id = started["step"]["id"]
    accumulator.complete_step(
        step_id,
        tool_calls=[{"id": "call-1", "name": "execute", "args": {"command": "x"}}],
    )
    waiting = accumulator.terminal_snapshot(status="interrupted")
    assert waiting[0]["status"] == "waiting"
    assert waiting[0]["tool_calls"][0]["status"] == "waiting"

    resumed = AssistantStepAccumulator(
        message_id="assistant-1", run_id="run-2", steps=waiting
    )
    result = resumed.project_tool_result(call_id="call-1", result="ok")
    assert result is not None
    assert resumed.steps[0]["tool_calls"][0]["status"] == "completed"
    assert resumed.steps[0]["status"] == "completed"

    final = resumed.terminal_snapshot(status="completed", final_content="完成")
    assert final[0]["status"] == "completed"
    assert final[0]["tool_calls"][0]["status"] == "completed"
    assert final[0]["is_final"] is False


def test_tool_snapshots_carry_server_side_timings():
    """工具耗时只能来自真实观测：结果晚到也不能把调用时间编造出来。"""

    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    accumulator.start_step()
    event = accumulator.project_tool_call(
        call_id="call-1",
        name="execute",
        batch_index=0,
        started_at=1_000,
    )
    assert event is not None
    assert event["call"]["started_at"] == 1_000
    assert "completed_at" not in event["call"]

    accumulator.project_tool_result(
        call_id="call-1",
        result="ok",
        completed_at=1_250,
    )
    snapshot = accumulator.steps[0]["tool_calls"][0]
    assert snapshot["started_at"] == 1_000
    assert snapshot["completed_at"] == 1_250

    # 只有结果、没有调用事件：不写 started_at，前端据此不显示耗时。
    orphan = AssistantStepAccumulator(message_id="assistant-1", run_id="run-2")
    orphan.start_step()
    orphan.project_tool_result(call_id="call-2", result="late", completed_at=2_000)
    orphan_tool = orphan.steps[0]["tool_calls"][0]
    assert "started_at" not in orphan_tool
    assert orphan_tool["completed_at"] == 2_000


def test_completed_snapshot_converges_waiting_without_tool_result():
    """工具结果事件丢失时，完成的 step 也不能停留在 waiting。"""

    accumulator = AssistantStepAccumulator(message_id="assistant-1", run_id="run-1")
    started = accumulator.start_step()
    step_id = started["step"]["id"]
    accumulator.complete_step(
        step_id,
        tool_calls=[{"id": "call-1", "name": "execute", "args": {"command": "x"}}],
    )
    waiting = accumulator.terminal_snapshot(status="interrupted")

    final = AssistantStepAccumulator(
        message_id="assistant-1", run_id="run-2", steps=waiting
    ).terminal_snapshot(status="completed", final_content="完成")
    assert final[0]["status"] == "completed"
