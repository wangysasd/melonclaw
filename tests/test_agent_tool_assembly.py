"""用应用真实工具装配验证主/子 Agent 的聊天、选择与搜索执行。"""

import asyncio
import json

import httpx
import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.core import agent as agent_module
from melonclaw.core.agent import build_research_agent
from melonclaw.core.agent_controls import TODO_GUIDANCE
from melonclaw.core.chat_model import ProviderChatOpenAI
from melonclaw.core.config import Settings
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.core.tool_catalog import catalog_fingerprint
from melonclaw.tool import search as search_module


def test_application_tools_support_chat_search_and_subagent_discovery(monkeypatch, tmp_path):
    bindings, selections, searches = [], [], []

    class Tavily:
        def __init__(self, *, api_key):
            pass

        def search(self, **kwargs):
            searches.append(kwargs)
            return {"results": [{"title": "离线资料", "content": kwargs["query"]}]}

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible_names=[item.name for item in tools])

        def with_structured_output(self, schema, **kwargs):
            async def select(messages):
                selections.append(messages[-1].content)
                return {"tools": ["internet_search"]}

            return RunnableLambda(select)

        async def _agenerate(self, messages, *, visible_names, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            bindings.append((user.content, set(visible_names)))
            results = [m for m in messages[messages.index(user) + 1:] if isinstance(m, ToolMessage)]
            if user.content == "普通问答" or results and results[-1].name in {"internet_search", "task"}:
                message = AIMessage(content="完成")
            elif user.content == "委派搜索":
                message = AIMessage(content="", tool_calls=[{
                    "name": "task", "id": "delegate", "args": {
                        "description": "查找子任务资料", "subagent_type": "general-purpose",
                    },
                }])
            elif "internet_search" in visible_names:
                message = AIMessage(content="", tool_calls=[{
                    "name": "internet_search", "id": f"search-{len(searches)}",
                    "args": {"query": user.content},
                }])
            else:
                message = AIMessage(content="", tool_calls=[{
                    "name": "find_tools", "id": f"find-{len(selections)}",
                    "args": {"query": user.content},
                }])
            return ChatResult(generations=[ChatGeneration(message=message)])

    model = Model(responses=[])
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(search_module, "TavilyClient", Tavily)

    async def run():
        settings = Settings(workspace_root=tmp_path, data_root=tmp_path / "data", agent_usage_enabled=False)
        resolved = ResolvedModel(profile_id="offline", display_name="离线模型", source="custom",
            adapter_type="openai", provider="offline", model_name="offline", base_url=None,
            api_key="offline-placeholder")
        agent = await build_research_agent(settings, workspace_dir=tmp_path / "workspace", model=resolved,
            checkpointer=InMemorySaver(), runtime_backend=StateBackend())
        config = {"configurable": {"thread_id": "application-tools"}}
        for index, content in enumerate(["普通问答", "查找资料", "委派搜索"]):
            result = await agent.ainvoke({"messages": [HumanMessage(id=f"u{index}", content=content)]}, config)
            assert result["messages"][-1].content == "完成"
            if index == 0:
                assert not selections and not searches
                assert "internet_search" not in bindings[0][1]
        assert [item["query"] for item in searches] == ["查找资料", "查找子任务资料"]
        assert len(selections) == 2
        assert "查找资料" in selections[0] and "查找子任务资料" in selections[1]
        child_bindings = [names for content, names in bindings if content == "查找子任务资料"]
        assert "internet_search" not in child_bindings[0]
        assert "internet_search" in child_bindings[-1]
        assert (await agent.aget_state(config)).values["tool_pool"]["names"] == ["internet_search"]

    asyncio.run(run())


@pytest.mark.parametrize('scenario', ['hidden_catalog', 'visible_catalog', 'long_history'])
def test_summary_counts_visible_catalog_and_still_compacts_history(monkeypatch, tmp_path, scenario):
    calls, bindings = [], []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible_names={item.name for item in tools})

        async def _agenerate(self, messages, **kwargs):
            calls.append('main' if 'visible_names' in kwargs else 'summary')
            if 'visible_names' in kwargs:
                bindings.append(kwargs['visible_names'])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='done'))])

    def catalog_tool():
        return 'offline'

    catalog = [StructuredTool.from_function(catalog_tool, description='large schema ' * 10000)]
    fingerprint = catalog_fingerprint(catalog)

    async def build_tools(*args, **kwargs):
        return catalog

    model = Model(responses=[])
    monkeypatch.setattr(agent_module, 'build_chat_model', lambda *args, **kwargs: model)
    monkeypatch.setattr(agent_module, 'build_agent_tools', build_tools)

    async def run():
        settings = Settings(workspace_root=tmp_path, data_root=tmp_path / 'data',
                            agent_usage_enabled=False, agent_output_reserve=128,
                            agent_summary_keep_tokens=500)
        resolved = ResolvedModel(profile_id='offline', display_name='offline', source='custom',
            adapter_type='openai_compatible', provider='offline', model_name='offline',
            base_url=None, api_key='offline-placeholder', context_window=16000)
        agent = await build_research_agent(settings, workspace_dir=tmp_path / 'workspace',
            model=resolved, checkpointer=InMemorySaver(), runtime_backend=StateBackend())
        config = {'configurable': {'thread_id': 'summary-catalog'}}
        history_pairs = 20 if scenario == 'long_history' else 1
        messages = [message for index in range(history_pairs) for message in (
            HumanMessage(id=f'old-{index}', content='old ' * 500),
            AIMessage(id=f'reply-{index}', content='reply ' * 500),
        )]
        messages.append(HumanMessage(id='new', content='hello'))
        if scenario == 'visible_catalog':
            await agent.aupdate_state(config, {
                'messages': messages,
                'tool_pool': {'fingerprint': fingerprint, 'turn_id': 'new',
                              'names': ['catalog_tool'], 'processed': [], 'outcome': 'selected'},
            }, as_node='ToolPoolMiddleware.before_model')
        result = await agent.ainvoke({'messages': messages}, config)
        assert result['messages'][-1].content == 'done'
        assert calls == (['main'] if scenario == 'hidden_catalog' else ['summary', 'main'])
        assert ('catalog_tool' in bindings[-1]) == (scenario == 'visible_catalog')

    asyncio.run(run())


def test_child_summary_does_not_count_hidden_catalog(monkeypatch, tmp_path):
    summaries, child_calls = [], []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible_names={item.name for item in tools})

        async def _agenerate(self, messages, **kwargs):
            if 'visible_names' not in kwargs:
                summaries.append(1)
                message = AIMessage(content='summary')
            elif '你是 MelonClaw' in messages[0].text:
                if any(isinstance(m, ToolMessage) and m.name == 'task' for m in messages):
                    message = AIMessage(content='done')
                else:
                    message = AIMessage(content='', tool_calls=[{
                        'name': 'task', 'id': 'delegate', 'args': {
                            'description': 'child task', 'subagent_type': 'general-purpose'},
                    }])
            else:
                assert 'catalog_tool' not in kwargs['visible_names']
                child_calls.append(1)
                message = (AIMessage(content='', tool_calls=[{
                    'name': 'list_mcp_tools', 'id': f'history-{len(child_calls)}', 'args': {},
                }]) if len(child_calls) < 3 else AIMessage(content='child done'))
            return ChatResult(generations=[ChatGeneration(message=message)])

    def catalog_tool():
        return 'offline'

    def list_mcp_tools():
        return 'child history ' * 500

    catalog = [StructuredTool.from_function(catalog_tool, description='large schema ' * 10000),
               StructuredTool.from_function(list_mcp_tools, description='Read offline history.')]

    async def build_tools(*args, **kwargs):
        return catalog

    model = Model(responses=[])
    monkeypatch.setattr(agent_module, 'build_chat_model', lambda *args, **kwargs: model)
    monkeypatch.setattr(agent_module, 'build_agent_tools', build_tools)

    async def run():
        settings = Settings(workspace_root=tmp_path, data_root=tmp_path / 'data',
                            agent_usage_enabled=False, agent_output_reserve=128,
                            agent_summary_keep_tokens=500)
        resolved = ResolvedModel(profile_id='offline', display_name='offline', source='custom',
            adapter_type='openai_compatible', provider='offline', model_name='offline',
            base_url=None, api_key='offline-placeholder', context_window=16000)
        agent = await build_research_agent(settings, workspace_dir=tmp_path / 'workspace',
            model=resolved, checkpointer=InMemorySaver(), runtime_backend=StateBackend())
        result = await agent.ainvoke({'messages': [HumanMessage(id='user', content='delegate')]},
                                    {'configurable': {'thread_id': 'child-summary'}})
        assert result['messages'][-1].content == 'done'
        assert len(child_calls) == 3 and summaries == []

    asyncio.run(run())


@pytest.mark.parametrize('todo_enabled', [True, False])
def test_todo_switch_overrides_codex_profile_in_main_and_child(monkeypatch, tmp_path, todo_enabled):
    bindings = []

    def respond(request):
        body = json.loads(request.content)
        messages = body['messages']
        content = messages[0]['content']
        system_text = (''.join(block['text'] for block in content if block['type'] == 'text')
                       if isinstance(content, list) else content)
        scope = 'main' if '你是 MelonClaw' in system_text else 'child'
        names = {item['function']['name'] for item in body['tools']}
        bindings.append((scope, names))
        assert ('write_todos' in names) == todo_enabled
        assert (TODO_GUIDANCE in system_text) == todo_enabled
        tool_names = {call['id']: call['function']['name'] for item in messages
                      for call in item.get('tool_calls', [])}
        results = {tool_names[item['tool_call_id']] for item in messages if item['role'] == 'tool'}
        call = None
        if todo_enabled and 'write_todos' not in results:
            call = {'name': 'write_todos', 'args': {'todos': [
                {'content': f'{scope} work', 'status': 'in_progress'}]}}
        elif scope == 'main' and 'task' not in results:
            call = {'name': 'task', 'args': {'description': 'child task', 'subagent_type': 'general-purpose'}}
        message = {'role': 'assistant', 'content': 'done'}
        if call:
            message = {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': f'{scope}-{call["name"]}', 'type': 'function',
                'function': {'name': call['name'], 'arguments': json.dumps(call['args'])},
            }]}
        return httpx.Response(200, json={
            'id': f'{scope}-response', 'object': 'chat.completion', 'model': 'gpt-5.2-codex',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if call else 'stop', 'message': message}],
        })

    async def build_tools(*args, **kwargs):
        return []

    monkeypatch.setattr(agent_module, 'build_agent_tools', build_tools)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ProviderChatOpenAI(model='gpt-5.2-codex', api_key='offline-placeholder',
                base_url='https://offline.invalid/v1', http_async_client=client, max_retries=0,
                use_responses_api=False)
            monkeypatch.setattr(agent_module, 'build_chat_model', lambda *args, **kwargs: model)
            settings = Settings(workspace_root=tmp_path, data_root=tmp_path / 'data',
                                agent_usage_enabled=False, agent_todo_enabled=todo_enabled)
            resolved = ResolvedModel(profile_id='offline', display_name='offline', source='custom',
                adapter_type='openai_compatible', provider='offline', model_name='gpt-5.2-codex',
                base_url='https://offline.invalid/v1', api_key='offline-placeholder')
            agent = await build_research_agent(settings, workspace_dir=tmp_path / 'workspace',
                model=resolved, checkpointer=InMemorySaver(), runtime_backend=StateBackend())
            config = {'configurable': {'thread_id': 'todo-switch'}}
            # 同一个任务 ID，验证关闭后的清理不依赖新任务触发预算重置。
            await agent.aupdate_state(config, {
                'messages': [HumanMessage(id='user', content='delegate')],
                'budget_task_id': 'user',
                'todos': [{'content': 'stale work', 'status': 'pending'}],
            }, as_node='model')
            result = await agent.ainvoke({'messages': [HumanMessage(id='user', content='delegate')]}, config)
            assert result['messages'][-1].content == 'done'
            assert result['todos'] == ([{'content': 'main work', 'status': 'in_progress'}]
                                       if todo_enabled else [])
            assert {scope for scope, _ in bindings} == {'main', 'child'}

    asyncio.run(run())
