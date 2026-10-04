"""恢复沿用计时与内容顺序；推理独立保留，不能成为旧轮次最终答复。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from melonclaw.output.assistant_steps import AssistantStepAccumulator
from melonclaw.services.execution import ExecutionService
from melonclaw.services.execution_trace import ExecutionTrace


def test_resume_timings_and_cancel_keep_completed_activities():
    previous = {"preparation_ms": 10, "activities": [{"id": "old", "kind": "model", "status": "completed", "duration_ms": 20}]}
    events = [{"type": "subagent_text", "subagent_id": "child", "content_kind": "reasoning", "text": "之前的分析"}]
    trace = ExecutionTrace(5, previous, events)
    trace.remember({"type": "subagent_tool_result", "subagent_id": "child", "content": "ok"})
    trace.remember({"type": "subagent_text", "subagent_id": "child", "content_kind": "text", "text": "之后的说明"})
    trace.remember({"type": "run_activity", "id": "new", "kind": "model", "status": "started"})
    trace.close()
    assert trace.timings["preparation_ms"] == 15
    assert [item["status"] for item in trace.timings["activities"]] == ["completed", "unknown"]
    assert len(previous["activities"]) == 1
    assert [event["type"] for event in trace.events] == ["subagent_text", "subagent_tool_result", "subagent_text"]
    assert len(events) == 1


def test_reasoning_deltas_replay_and_failure_do_not_reuse_old_answer():
    async def run():
        current = AIMessage(content=[{"type": "reasoning", "reasoning": "分析"}])

        async def aget_state(config):
            return SimpleNamespace(values={"messages": [AIMessage(content="旧答复"), HumanMessage(content="新需求"), current]})

        service = ExecutionService(SimpleNamespace(), SimpleNamespace())
        assert await service._latest_root_assistant_text(SimpleNamespace(aget_state=aget_state), {}) == ""
    asyncio.run(run())
    accumulator = AssistantStepAccumulator(message_id="a", run_id="r")
    step_id = accumulator.start_step()["step"]["id"]
    accumulator.apply_event({"type": "assistant_text_delta", "step_id": step_id, "content_kind": "reasoning", "delta": "分析"})
    assert accumulator.visible_content() == ""
    snapshot = accumulator.terminal_snapshot(status="failed")
    assert snapshot[0]["content_blocks"] == [{"type": "reasoning", "text": "分析"}]
