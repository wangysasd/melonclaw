"""无人值守结果投影、异常收敛与真实框架 interrupt 检查。"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt
from test_task_agent import Model, build, settings_for

from melonclaw.core.agent import AgentContext
from melonclaw.core.task_policy import TaskRunControl
from melonclaw.services.task_execution import TaskProgress, execute_task_agent


def execute(agent, settings, control=None, **kwargs):
    return execute_task_agent(
        agent,
        run_id=uuid4(),
        instruction="test",
        context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
        control=control or TaskRunControl(20, 20),
        settings=settings,
        **kwargs,
    )


def test_public_projection_discards_thinking_and_redacts_secrets(monkeypatch, tmp_path):
    async def run():
        message = AIMessage(
            content=[
                {"type": "reasoning", "reasoning": "do not publish"},
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "text", "text": "正文 api_key=example-secret"},
            ],
            additional_kwargs={"reasoning_content": "private"},
        )
        agent, control, settings = await build(monkeypatch, tmp_path, Model(responses=[message]))
        result = await execute(agent, settings, control)
        assert result.status == "succeeded"
        assert result.result_markdown == "正文 api_key=<redacted>"

    asyncio.run(run())


def test_tool_payloads_and_subagent_text_never_become_public_result():
    progress = TaskProgress(1000)
    progress.consume(("task",), {"model": {"messages": [AIMessage(content="private child")]}})
    progress.consume(
        (),
        {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="private tool output",
                        tool_call_id="id",
                        name="internet_search",
                    )
                ]
            }
        },
    )
    assert not progress.text


def test_overflow_is_failed_and_partial(monkeypatch, tmp_path):
    async def run():
        settings = settings_for(tmp_path, task_result_max_chars=10)
        agent, control, _ = await build(
            monkeypatch,
            tmp_path,
            Model(responses=[AIMessage(content="report" * 20)]),
            settings=settings,
        )
        result = await execute(agent, settings, control)
        assert result.status == "failed" and result.error_code == "result_limit"
        assert result.result_is_partial and len(result.result_markdown) == 10

    asyncio.run(run())


def test_real_interrupt_never_resumes_or_reports_success(tmp_path):
    calls = []

    @tool
    def unexpected_interaction() -> str:
        """Request input in a regression fixture."""
        answer = interrupt({"unknown_future_kind": "please approve"})
        calls.append(answer)
        return "approved"

    async def run():
        model = Model(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "unexpected_interaction",
                            "args": {},
                            "id": "i",
                        }
                    ],
                )
            ]
        )
        agent = create_agent(model, tools=[unexpected_interaction], checkpointer=InMemorySaver())
        result = await execute(agent, settings_for(tmp_path))
        assert result.status == "failed" and result.error_code == "interaction_required"
        assert not calls

    asyncio.run(run())


class SlowAgent:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def astream(self, *args, **kwargs):
        try:
            yield (
                (),
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="部分内容",
                                tool_calls=[
                                    {
                                        "name": "task",
                                        "args": {},
                                        "id": "pending",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            self.started.set()
            await asyncio.Event().wait()
        finally:
            self.closed.set()


def test_cancel_confirms_local_execution_stopped_and_preserves_partial(tmp_path):
    async def run():
        agent = SlowAgent()
        task = asyncio.create_task(execute(agent, settings_for(tmp_path)))
        await agent.started.wait()
        task.cancel()
        result = await task
        assert agent.closed.is_set()
        assert result.status == "cancelled" and result.result_is_partial
        assert result.result_markdown == "部分内容"

    asyncio.run(run())


def test_timeout_stops_execution(tmp_path):
    async def run():
        agent = SlowAgent()
        result = await execute(agent, settings_for(tmp_path, task_execution_timeout_seconds=1))
        assert agent.closed.is_set()
        assert result.error_code == "execution_timeout"

    asyncio.run(run())


def test_failure_after_partial_answer_does_not_leak_exception(tmp_path):
    class Failing:
        async def astream(self, *args, **kwargs):
            yield (), {"model": {"messages": [AIMessage(content="部分报告")]}}
            raise RuntimeError("password=never-return-this")

    result = asyncio.run(execute(Failing(), settings_for(tmp_path)))
    assert result.status == "failed" and result.result_markdown == "部分报告"
    assert "never-return" not in result.error_message


def test_empty_final_answer_does_not_promote_intermediate_text(tmp_path):
    class NoFinal:
        async def astream(self, *args, **kwargs):
            yield (
                (),
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="还没完成",
                                tool_calls=[
                                    {
                                        "name": "internet_search",
                                        "args": {},
                                        "id": "search",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            yield (
                (),
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content=[
                                    {"type": "reasoning", "reasoning": "not an answer"},
                                ]
                            )
                        ]
                    }
                },
            )

        async def aget_state(self, config):
            return SimpleNamespace(next=(), interrupts=())

    result = asyncio.run(execute(NoFinal(), settings_for(tmp_path)))
    assert result.status == "failed" and result.error_code == "empty_result"
    assert result.result_markdown == "还没完成" and result.result_is_partial
