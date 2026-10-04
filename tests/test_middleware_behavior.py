"""新增中间件的行为验收：运行真实图，核对状态、事件与副作用次数。"""

import asyncio
from dataclasses import dataclass

import httpx
import openai
import pytest
from deepagents.backends import StateBackend
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from melonclaw.core import agent as agent_module
from melonclaw.core.agent import build_research_agent
from melonclaw.core.agent_controls import build_agent_controls
from melonclaw.core.agent_errors import execution_error_code
from melonclaw.core.chat_model import ModelInvocationError, ProviderChatOpenAI
from melonclaw.core.config import Settings
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.output.events import iter_research_events


@dataclass(frozen=True)
class TaskContext:
    user_message_id: str


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_controls(model, tmp_path, **kwargs):
    settings = Settings(
        workspace_root=tmp_path,
        data_root=tmp_path / "data",
        agent_usage_enabled=False,
        agent_retry_initial_delay=0,
        agent_retry_max_delay=0,
        **kwargs,
    )
    return build_agent_controls(model, StateBackend(), settings)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_hitl_resume_preserves_budget_and_todos_then_new_task_resets(tmp_path, decision):
    async def run():
        executed = []

        @tool
        def protected_write(value: str) -> str:
            """Record a controlled test write in memory."""
            executed.append(value)
            return "recorded"

        todos = [{"content": "检查审批恢复", "status": "in_progress"}]
        model = OfflineModel(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "write_todos", "args": {"todos": todos}, "id": "todo",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "protected_write", "args": {"value": "approved"}, "id": "write",
            }]),
            AIMessage(content="task finished"),
            AIMessage(content="new task finished"),
        ])
        agent = create_agent(
            model,
            tools=[protected_write],
            middleware=[
                *build_controls(model, tmp_path, agent_model_call_limit=3, agent_tool_call_limit=2),
                HumanInTheLoopMiddleware(interrupt_on={"protected_write": True}),
            ],
            context_schema=TaskContext,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "hitl-behavior"}}
        context = TaskContext(user_message_id="business-u1")
        paused = await agent.ainvoke(
            {"messages": [HumanMessage(id="graph-u1", content="执行受控任务")]},
            config, context=context,
        )
        assert paused["__interrupt__"]
        assert executed == []
        before = (await agent.aget_state(config)).values
        assert before["budget_task_id"] == "business-u1"
        # HITL 的 after_model 先中断；待审批的第二个模型步骤在恢复后补计。
        assert before["thread_model_call_count"] == 1
        assert before["todos"] == todos

        result = await agent.ainvoke(
            Command(resume={"decisions": [{"type": decision}]}), config, context=context,
        )
        after = (await agent.aget_state(config)).values
        assert result["messages"][-1].content == "task finished"
        assert executed == (["approved"] if decision == "approve" else [])
        assert after["budget_task_id"] == "business-u1"
        assert after["thread_model_call_count"] == 3
        # 官方拒绝分支保留请求并添加 error ToolMessage；预算计请求，副作用仍为零。
        assert after["thread_tool_call_count"]["__all__"] == 2
        assert after["todos"] == todos
        tool_result = next(m for m in result["messages"]
                           if isinstance(m, ToolMessage) and m.tool_call_id == "write")
        assert tool_result.status == ("success" if decision == "approve" else "error")

        result = await agent.ainvoke(
            {"messages": [HumanMessage(id="graph-u2", content="开始新任务")]}, config,
            context=TaskContext(user_message_id="business-u2"),
        )
        new_task = (await agent.aget_state(config)).values
        assert result["messages"][-1].content == "new task finished"
        assert new_task["budget_task_id"] == "business-u2"
        assert new_task["thread_model_call_count"] == 1
        assert new_task["thread_tool_call_count"] == {}
        assert new_task["todos"] == []

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_hitl_resume_cannot_make_another_model_call_after_budget_is_exhausted(tmp_path, decision):
    async def run():
        calls, executed = [], []

        @tool
        def protected_write() -> str:
            """Record a controlled test write in memory."""
            executed.append(1)
            return "recorded"

        class Counting(OfflineModel):
            async def _agenerate(self, messages, **kwargs):
                calls.append(1)
                return await super()._agenerate(messages, **kwargs)

        model = Counting(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "protected_write", "args": {}, "id": "write",
            }]),
            AIMessage(content="new task finished"),
        ])
        agent = create_agent(
            model, tools=[protected_write],
            middleware=[
                *build_controls(model, tmp_path, agent_model_call_limit=1),
                HumanInTheLoopMiddleware(interrupt_on={"protected_write": True}),
            ],
            context_schema=TaskContext, checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "hitl-budget"}}
        context = TaskContext(user_message_id="business-u1")
        result = await agent.ainvoke(
            {"messages": [HumanMessage(id="graph-u1", content="执行受控任务")]},
            config, context=context,
        )
        assert result["__interrupt__"]
        assert len(calls) == 1 and executed == []
        with pytest.raises(ModelCallLimitExceededError) as caught:
            await agent.ainvoke(
                Command(resume={"decisions": [{"type": decision}]}), config, context=context,
            )
        assert execution_error_code(caught.value) == "agent_call_limit_exceeded"
        assert len(calls) == 1
        assert executed == ([1] if decision == "approve" else [])
        state = (await agent.aget_state(config)).values
        assert state["budget_task_id"] == "business-u1"
        assert state["thread_model_call_count"] == 1

        result = await agent.ainvoke(
            {"messages": [HumanMessage(id="graph-u2", content="新任务")]}, config,
            context=TaskContext(user_message_id="business-u2"),
        )
        assert result["messages"][-1].content == "new task finished"
        assert len(calls) == 2
        assert (await agent.aget_state(config)).values["thread_model_call_count"] == 1

    asyncio.run(run())


def test_business_message_id_controls_budget_when_graph_message_ids_change(tmp_path):
    async def run():
        model = OfflineModel(responses=[AIMessage(content="done")])
        agent = create_agent(
            model, middleware=build_controls(model, tmp_path),
            context_schema=TaskContext, checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "business-task"}}
        for index, (business_id, count) in enumerate([
            ("business-u1", 1), ("business-u1", 2), ("business-u2", 1),
        ]):
            await agent.ainvoke(
                {"messages": [HumanMessage(id=f"graph-{index}", content="继续")]}, config,
                context=TaskContext(user_message_id=business_id),
            )
            state = (await agent.aget_state(config)).values
            assert state["budget_task_id"] == business_id
            assert state["thread_model_call_count"] == count

    asyncio.run(run())


@pytest.mark.parametrize("status,expected_attempts", [(429, 3), (503, 3), (401, 1)])
def test_retry_exhaustion_and_auth_failure_have_exact_attempt_counts(
    tmp_path, status, expected_attempts,
):
    async def run():
        attempts = []
        request = httpx.Request("POST", "https://offline.invalid/v1/chat/completions")
        cause = openai.APIStatusError(
            "offline failure", response=httpx.Response(status, request=request), body=None,
        )

        class Failing(OfflineModel):
            async def _agenerate(self, messages, **kwargs):
                attempts.append(1)
                raise ModelInvocationError("模型请求失败。") from cause

        model = Failing(responses=[])
        agent = create_agent(model, middleware=build_controls(model, tmp_path))
        with pytest.raises(ModelInvocationError) as caught:
            await agent.ainvoke({"messages": [HumanMessage(id="u1", content="hello")]})
        assert len(attempts) == expected_attempts
        assert execution_error_code(caught.value) == "model_execution_failed"

    asyncio.run(run())


@pytest.mark.parametrize("fragment_kind", ["text", "reasoning", "tool_args"])
def test_real_stream_failure_after_fragment_never_retries_or_executes_tools(
    monkeypatch, tmp_path, fragment_kind,
):
    async def run():
        attempts, executed, events = [], [], []

        @tool
        def read_data() -> str:
            """Read controlled test data."""
            executed.append(1)
            return "data"

        async def stream(self, *args, **kwargs):
            attempts.append(1)
            if fragment_kind == "tool_args":
                chunk = AIMessageChunk(content="", tool_call_chunks=[{
                    "name": "read_data", "args": "{", "id": "incomplete", "index": 0,
                }])
            elif fragment_kind == "reasoning":
                chunk = AIMessageChunk(content=[{
                    "type": "reasoning", "reasoning": "PARTIAL_REASONING", "index": 0,
                }], response_metadata={"output_version": "v1"})
            else:
                chunk = AIMessageChunk(content="PARTIAL_BODY")
            yield ChatGenerationChunk(message=chunk)
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://offline.invalid"))

        monkeypatch.setattr(ChatOpenAI, "_astream", stream)
        model = ProviderChatOpenAI(
            model="offline", api_key="offline-placeholder", max_retries=0, use_responses_api=False,
        )
        agent = create_agent(model, tools=[read_data], middleware=build_controls(model, tmp_path))
        agent.melonclaw_usage_enabled = True
        with pytest.raises(ModelInvocationError) as caught:
            async for event in iter_research_events(
                agent, {"messages": [HumanMessage(id="u1", content="hello")]}, {},
            ):
                events.append(event)
        assert caught.value.partial_output
        assert len(attempts) == 1
        assert executed == []
        assert not any(event["type"] == "tool_result" for event in events)
        visible = "".join(str(event.get("delta", "")) for event in events)
        if fragment_kind == "text":
            assert visible.count("PARTIAL_BODY") == 1
        elif fragment_kind == "reasoning":
            assert visible.count("PARTIAL_REASONING") == 1

    asyncio.run(run())


@pytest.mark.parametrize("usage_enabled", [True, False])
def test_application_usage_switch_emits_expected_events_without_extra_model_calls(
    monkeypatch, tmp_path, usage_enabled,
):
    calls = []

    class Answering(OfflineModel):
        async def _agenerate(self, messages, **kwargs):
            calls.append(1)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="可见答案", usage_metadata={
                    "input_tokens": 7, "output_tokens": 3, "total_tokens": 10,
                },
            ))])

    async def build_tools(*args, **kwargs):
        return []

    model = Answering(responses=[])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(agent_module, "build_agent_tools", build_tools)

    async def run():
        settings = Settings(
            workspace_root=tmp_path, data_root=tmp_path / "data", agent_usage_enabled=usage_enabled,
        )
        resolved = ResolvedModel(
            profile_id="offline", display_name="offline", source="custom", adapter_type="openai",
            provider="offline", model_name="offline", base_url=None, api_key="offline-placeholder",
        )
        agent = await build_research_agent(
            settings, workspace_dir=tmp_path / "workspace", model=resolved,
            checkpointer=InMemorySaver(), runtime_backend=StateBackend(),
        )
        events = [event async for event in iter_research_events(
            agent, {"messages": [HumanMessage(id="u1", content="普通问答")]},
            {"configurable": {"thread_id": "usage-switch"}},
        )]
        assert len(calls) == 1
        assert "可见答案" in "".join(str(event.get("delta", "")) for event in events)
        context_events = [event for event in events if event["type"] == "context_usage"]
        usage_events = [event for event in events if event["type"] == "model_usage"]
        if usage_enabled:
            assert len(context_events) == 1
            assert context_events[0]["scope"] == "main"
            assert context_events[0]["estimated_input_tokens"] > 0
            assert context_events[0]["context_window"] == resolved.context_window
            assert context_events[0]["summary_trigger_tokens"] == settings.summary_trigger_tokens(
                resolved.context_window,
            )
            completed = [event for event in usage_events if event["status"] == "completed"]
            assert len(completed) == 1
            assert completed[0]["input_tokens"] == 7
            assert completed[0]["output_tokens"] == 3
        else:
            assert context_events == usage_events == []

    asyncio.run(run())
