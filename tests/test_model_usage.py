"""用量事件覆盖内部调用，审批历史去重，缺失用量不能被记成零。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core.agent_controls import build_agent_controls
from melonclaw.core.config import Settings
from melonclaw.core.model_usage import ModelUsageCallback
from melonclaw.middleware.tool_selection import ToolPoolMiddleware
from melonclaw.output.events import iter_research_events
from melonclaw.services.execution_trace import ExecutionTrace
from melonclaw.tool.tool_discovery import build_tool_discovery


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_callback_unknown_usage_and_trace_resume_dedup():
    async def run():
        trace = ExecutionTrace(0)
        callback = ModelUsageCallback(trace.remember, model_id='db-model')
        run_id = uuid4()
        await callback.on_chat_model_start({}, [[HumanMessage(content='secret request')]], run_id=run_id,
                                           metadata={'lc_source': 'summarization'})
        await callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content='summary'))]]), run_id=run_id)
        assert len(trace.events) == 1
        assert trace.events[0]['input_tokens'] is None and trace.events[0]['kind'] == 'summary'
        assert 'secret request' not in str(trace.metadata())
        resumed = ExecutionTrace(1, timings=trace.timings, events=trace.events)
        resumed.remember(trace.events[0])
        assert len(resumed.events) == 1
        next_id = uuid4()
        callback = ModelUsageCallback(resumed.remember)
        await callback.on_chat_model_start({}, [[HumanMessage(content='hello')]], run_id=next_id)
        await callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content='ok', usage_metadata={
            'input_tokens': 12, 'output_tokens': 4, 'total_tokens': 16, 'input_token_details': {'cache_read': 8}}))]]), run_id=next_id)
        assert resumed.events[-1]['input_tokens'] == 12 and resumed.events[-1]['cache_read_tokens'] == 8
        assert len(resumed.events) == 2
    asyncio.run(run())


def test_real_stream_observes_summary_but_does_not_show_summary_text():
    async def run():
        class Summarizing(Model):
            async def _agenerate(self, messages, **kwargs):
                is_summary = len(messages) == 1 and 'summar' in str(messages[0].content).lower()
                message = AIMessage(content='INTERNAL_SUMMARY' if is_summary else 'final answer', usage_metadata={
                    'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})
                return ChatResult(generations=[ChatGeneration(message=message)])
        model = Summarizing(responses=[])
        settings = Settings(workspace_root=Path('/tmp'), agent_output_reserve=128,
                            agent_summary_keep_tokens=200)
        agent = create_deep_agent(model=model, middleware=build_agent_controls(model, StateBackend(), settings, context_window=2000), checkpointer=InMemorySaver())
        agent.melonclaw_usage_enabled = True
        messages = [HumanMessage(content='old data ' * 120, id=f'u{i}') for i in range(20)]
        messages.append(HumanMessage(content='answer now', id='latest'))
        events = [e async for e in iter_research_events(agent, {'messages': messages},
            {'configurable': {'thread_id': 'usage-summary'}}, context=SimpleNamespace(model_id='model'))]
        ledger = ExecutionTrace(0)
        for event in events:
            ledger.remember(event)
        usage = [e for e in ledger.events if e['type'] == 'model_usage']
        assert {e['kind'] for e in usage} == {'main', 'summary'}
        assert all(e['status'] == 'completed' and e['input_tokens'] == 100 for e in usage)
        visible = ''.join(str(e.get('delta', e.get('text', ''))) for e in events)
        assert 'INTERNAL_SUMMARY' not in visible and 'final answer' in visible
        assert any(e['type'] == 'context_usage' for e in events)
    asyncio.run(run())


def test_real_deep_agent_observes_child_calls_and_isolates_counters():
    async def run():
        @tool
        def read_data() -> str:
            """Read data."""
            return 'data'
        class Delegating(Model):
            async def _agenerate(self, messages, **kwargs):
                child = messages[0].content == 'child marker'
                if child or any(isinstance(m, ToolMessage) and m.name == 'task' for m in messages):
                    msg = AIMessage(content='done', usage_metadata={'input_tokens': 10, 'output_tokens': 3, 'total_tokens': 13})
                else:
                    msg = AIMessage(content='', tool_calls=[{'name': 'task', 'args': {'description': 'read', 'subagent_type': 'researcher'}, 'id': 'delegate'}])
                return ChatResult(generations=[ChatGeneration(message=msg)])
        model = Delegating(responses=[])
        backend = StateBackend()
        settings = Settings(workspace_root=Path('/tmp'))
        pool = ToolPoolMiddleware(model=model, catalog_tools=[read_data])
        tools = [read_data, build_tool_discovery()]
        agent = create_deep_agent(model=model, backend=backend, tools=tools,
            middleware=[*build_agent_controls(model, backend, settings), pool], checkpointer=InMemorySaver(),
            subagents=[{'name': 'researcher', 'description': 'Read data', 'system_prompt': 'child marker', 'tools': tools,
                        'middleware': build_agent_controls(model, backend, settings, scope='subagent')}])
        agent.melonclaw_usage_enabled = True
        events = [e async for e in iter_research_events(agent, {'messages': [HumanMessage(content='read', id='u')]},
            {'configurable': {'thread_id': 'usage-child'}})]
        assert {e['kind'] for e in events if e['type'] == 'model_usage'} == {'main', 'subagent'}
        assert {e['scope'] for e in events if e['type'] == 'context_usage'} == {'main', 'subagent'}
        snapshot = await agent.aget_state({'configurable': {'thread_id': 'usage-child'}})
        assert snapshot.values['thread_model_call_count'] == 2
        assert snapshot.values['thread_tool_call_count']['__all__'] == 1
    asyncio.run(run())


def test_actual_official_selector_reports_usage_in_internal_callback():
    import json

    import httpx
    from langchain_core.runnables.config import set_config_context

    from melonclaw.core.chat_model import ProviderChatOpenAI

    async def run():
        @tool
        def read_data() -> str:
            """Read data."""
            return 'ok'
        def respond(request):
            body = json.loads(request.content)
            assert body['response_format']['type'] == 'json_schema'
            return httpx.Response(200, json={
                'id': 'select', 'object': 'chat.completion', 'model': 'offline',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                    'role': 'assistant', 'content': '{"tools":["read_data"]}'}}],
                'usage': {'prompt_tokens': 45, 'completion_tokens': 8, 'total_tokens': 53},
            })
        trace = ExecutionTrace(0)
        callback = ModelUsageCallback(trace.remember)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ProviderChatOpenAI(model='offline', api_key='offline-placeholder',
                base_url='https://offline.invalid/v1', http_async_client=client, max_retries=0)
            pool = ToolPoolMiddleware(model=model, catalog_tools=[read_data])
            state = {'messages': [HumanMessage(id='u', content='read')],
                     'tool_requests': [{'id': 'find', 'turn_id': 'u', 'query': 'read data'}]}
            runtime = SimpleNamespace(context=None, stream_writer=lambda _: None)
            with set_config_context({'callbacks': [callback]}) as ctx:
                update = await asyncio.create_task(pool.abefore_model(state, runtime), context=ctx)
            assert update['tool_pool']['names'] == ['read_data']
        assert len(trace.events) == 1
        assert trace.events[0]['kind'] == 'selection'
        assert trace.events[0]['input_tokens'] == 45 and trace.events[0]['output_tokens'] == 8
    asyncio.run(run())
