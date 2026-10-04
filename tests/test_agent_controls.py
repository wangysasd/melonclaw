"""真实图验证任务预算、审批恢复、工具批次与重试的安全边界。"""

import asyncio
from pathlib import Path

import httpx
import openai
import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core.agent_controls import build_agent_controls
from melonclaw.core.agent_errors import execution_error_code, retry_transient_model_error
from melonclaw.core.chat_model import ModelInvocationError
from melonclaw.core.config import Settings, load_settings


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def controls(**kwargs):
    settings = Settings(workspace_root=Path('/tmp'), agent_usage_enabled=False, **kwargs)
    return build_agent_controls(Model(responses=[AIMessage(content='summary')]), StateBackend(), settings)


def test_task_limit_survives_resume_and_new_message_resets():
    async def run():
        @tool
        def read_data() -> str:
            """Read test data."""
            return 'ok'
        model = Model(responses=[
            AIMessage(content='', tool_calls=[{'name': 'read_data', 'args': {}, 'id': 'read'}]),
            AIMessage(content='answer'),
        ])
        agent = create_agent(model, tools=[read_data], middleware=controls(agent_model_call_limit=1),
                             checkpointer=InMemorySaver(), interrupt_before=['tools'])
        cfg = {'configurable': {'thread_id': 'test'}, 'recursion_limit': 100}
        await agent.ainvoke({'messages': [HumanMessage(content='read', id='u1')]}, cfg)
        state = (await agent.aget_state(cfg)).values
        assert state['thread_model_call_count'] == 1
        with pytest.raises(ModelCallLimitExceededError):
            await agent.ainvoke(None, cfg)
        result = await agent.ainvoke({'messages': [HumanMessage(content='new', id='u2')]}, cfg)
        assert result['messages'][-1].content == 'answer'
        state = (await agent.aget_state(cfg)).values
        assert state['budget_task_id'] == 'u2' and state['thread_model_call_count'] == 1
    asyncio.run(run())


def test_overflowing_parallel_tool_batch_executes_nothing():
    async def run():
        executed = []
        @tool
        def read_data() -> str:
            """Read test data."""
            executed.append('read')
            return 'ok'
        model = Model(responses=[AIMessage(content='', tool_calls=[
            {'name': 'read_data', 'args': {}, 'id': f'read{i}'} for i in range(2)])])
        agent = create_agent(model, tools=[read_data], middleware=controls(agent_tool_call_limit=1))
        with pytest.raises(ToolCallLimitExceededError) as caught:
            await agent.ainvoke({'messages': [HumanMessage(content='read', id='u')]})
        assert executed == []
        assert execution_error_code(caught.value) == 'agent_call_limit_exceeded'
    asyncio.run(run())


def test_transient_retries_are_bounded_and_partial_output_is_not_retried():
    async def run():
        attempts = []
        cause = openai.APIConnectionError(request=httpx.Request('POST', 'https://offline.invalid'))
        class Failing(Model):
            async def _agenerate(self, messages, **kwargs):
                attempts.append(1)
                if len(attempts) < 3:
                    raise ModelInvocationError('safe') from cause
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='ok'))])
        agent = create_agent(Failing(responses=[]), middleware=[ModelRetryMiddleware(
            max_retries=2, initial_delay=0, max_delay=0, retry_on=retry_transient_model_error, on_failure='error')])
        result = await agent.ainvoke({'messages': [HumanMessage(content='hello')]})
        assert result['messages'][-1].content == 'ok' and len(attempts) == 3
        partial = ModelInvocationError('safe', partial_output=True)
        partial.__cause__ = cause
        assert not retry_transient_model_error(partial)
        assert not retry_transient_model_error(ValueError('bad input'))
        for code in (400, 401, 403, 404, 429, 500, 502, 503, 504):
            error = openai.APIStatusError('safe', response=httpx.Response(code, request=cause.request), body=None)
            assert retry_transient_model_error(error) == (code in {429, 500, 502, 503, 504})
    asyncio.run(run())


def test_configuration_validates_relations_and_flags(monkeypatch, tmp_path):
    monkeypatch.setenv('MELONCLAW_WORKSPACE_DIR', str(tmp_path))
    monkeypatch.setenv('MELONCLAW_AGENT_OUTPUT_RESERVE', '4096')
    monkeypatch.setenv('MELONCLAW_AGENT_SUMMARY_TRIGGER_RATIO', '0.8')
    settings = load_settings()
    assert settings.summary_trigger_tokens(1_000_000) == 800_000
    monkeypatch.setenv('MELONCLAW_AGENT_TODO_ENABLED', 'false')
    assert not load_settings().agent_todo_enabled
    monkeypatch.setenv('MELONCLAW_AGENT_SUMMARY_KEEP_TOKENS', '30000')
    with pytest.raises(ValueError, match='保留'):
        load_settings().summary_trigger_tokens(32768)


def test_real_v3_stream_retries_failure_before_first_fragment(monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    from langchain_openai import ChatOpenAI

    from melonclaw.core.chat_model import ProviderChatOpenAI
    from melonclaw.output.events import iter_research_events

    async def run():
        attempts = []
        async def stream(self, *args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise openai.APIConnectionError(request=httpx.Request('POST', 'https://offline.invalid'))
            yield ChatGenerationChunk(message=AIMessageChunk(content='success'))
        monkeypatch.setattr(ChatOpenAI, '_astream', stream)
        model = ProviderChatOpenAI(model='offline', api_key='offline-placeholder', max_retries=0)
        agent = create_agent(model, middleware=controls(agent_retry_initial_delay=0, agent_retry_max_delay=0))
        agent.melonclaw_usage_enabled = True
        events = [e async for e in iter_research_events(agent, {'messages': [HumanMessage(content='hi', id='u')]}, {})]
        assert len(attempts) == 2
        assert 'success' in ''.join(e.get('delta', '') for e in events)
        assert {e['status'] for e in events if e['type'] == 'model_usage'} >= {'completed', 'failed'}
    asyncio.run(run())


def test_context_overflow_triggers_summary_fallback_without_transient_retry():
    from langchain_core.exceptions import ContextOverflowError

    async def run():
        seen = []
        class Overflow(Model):
            async def _agenerate(self, messages, **kwargs):
                seen.append(list(messages))
                if len(seen) == 1:
                    raise ContextOverflowError('too long')
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='done'))])
        agent = create_deep_agent(model=Overflow(responses=[]), middleware=controls(agent_summary_keep_tokens=128))
        messages = [HumanMessage(content='past ' * 120, id=f'u{i}') for i in range(12)]
        messages.append(HumanMessage(content='current question', id='current'))
        result = await agent.ainvoke({'messages': messages})
        assert result['messages'][-1].content == 'done'
        assert len(seen) == 2
        assert len(seen[-1]) < len(seen[0])
        assert seen[-1][-1].content == 'current question'
    asyncio.run(run())


def test_official_todo_is_optional_and_updates_graph_state():
    async def run():
        model = Model(responses=[AIMessage(content='', tool_calls=[{
            'name': 'write_todos', 'args': {'todos': [{'content': 'Read data', 'status': 'in_progress'}]}, 'id': 'todo'}]),
            AIMessage(content='ready')])
        agent = create_agent(model, middleware=controls())
        result = await agent.ainvoke({'messages': [HumanMessage(content='plan', id='u')]})
        assert result['todos'] == [{'content': 'Read data', 'status': 'in_progress'}]
        disabled = controls(agent_todo_enabled=False)
        disabled_agent = create_agent(Model(responses=[AIMessage(content='ready')]), middleware=disabled)
        assert 'tools' not in disabled_agent.nodes
    asyncio.run(run())


def test_different_model_windows_compute_independent_thresholds():
    settings = Settings(workspace_root=Path('/tmp'))
    assert settings.summary_trigger_tokens(1_000_000) == 800_000
    assert settings.summary_trigger_tokens(128_000) == 102_400
    assert settings.summary_trigger_tokens(32_768) == 26_214
    with pytest.raises(ValueError, match='输出预留'):
        settings.summary_trigger_tokens(4096)
    with pytest.raises(ValueError, match='保留'):
        Settings(workspace_root=Path('/tmp'), agent_summary_keep_tokens=900_000).summary_trigger_tokens(1_000_000)
