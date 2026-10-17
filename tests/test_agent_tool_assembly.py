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
from melonclaw.core.prompts import FILE_EXECUTION_GUIDANCE, MCP_EXECUTION_GUIDANCE
from melonclaw.core.tool_catalog import catalog_fingerprint
from melonclaw.tool import search as search_module
from melonclaw.tool import tools as tools_module


def test_application_tools_support_chat_search_and_subagent_discovery(monkeypatch, tmp_path):
    bindings, selections, searches, fetches = [], [], [], []

    def fake_search(query: str, count: int = 5, ori_question: str | None = None,
                    need_content: bool = False, *, api_key: str | None = None):
        searches.append({"query": query, "count": count, "api_key": api_key})
        return {"results": [{"title": "离线资料", "url": "https://example.com/a", "summary": query}]}

    def fake_fetch(url: str, *, api_key: str | None = None):
        fetches.append({"url": url, "api_key": api_key})
        return {"url": url, "title": "离线页面", "content": "# 离线正文", "content_truncated": False}

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible_names=[item.name for item in tools])

        def with_structured_output(self, schema, **kwargs):
            async def select(messages):
                selections.append(messages[-1].content)
                return {"tools": ["internet_search", "web_fetch"]}

            return RunnableLambda(select)

        async def _agenerate(self, messages, *, visible_names, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            bindings.append((user.content, set(visible_names)))
            results = [m for m in messages[messages.index(user) + 1:] if isinstance(m, ToolMessage)]
            if user.content == "普通问答" or results and results[-1].name in {"web_fetch", "task"}:
                message = AIMessage(content="完成")
            elif results and results[-1].name == "internet_search":
                message = AIMessage(content="", tool_calls=[{
                    "name": "web_fetch", "id": f"fetch-{len(fetches)}",
                    "args": {"url": "https://example.com/a"},
                }])
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
    monkeypatch.setattr(search_module, "internet_search", fake_search)
    monkeypatch.setattr(search_module, "web_fetch", fake_fetch)

    async def run():
        settings = Settings(workspace_root=tmp_path, data_root=tmp_path / "data",
                            deepseek_api_key="offline-deepseek-key", agent_usage_enabled=False)
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
                assert not selections and not searches and not fetches
                assert "internet_search" not in bindings[0][1]
                assert "web_fetch" not in bindings[0][1]
        assert [item["query"] for item in searches] == ["查找资料", "查找子任务资料"]
        assert [item["url"] for item in fetches] == ["https://example.com/a", "https://example.com/a"]
        assert len(selections) == 2
        assert "查找资料" in selections[0] and "查找子任务资料" in selections[1]
        child_bindings = [names for content, names in bindings if content == "查找子任务资料"]
        assert "internet_search" not in child_bindings[0]
        assert {"internet_search", "web_fetch"} <= child_bindings[-1]
        assert (await agent.aget_state(config)).values["tool_pool"]["names"] == ["internet_search", "web_fetch"]

    asyncio.run(run())


@pytest.mark.parametrize("delegate", [False, True])
def test_mcp_and_file_tools_execute_without_approval_but_protected_paths_are_denied(monkeypatch, tmp_path, delegate):
    executions = []

    async def external_write(value: str) -> str:
        """模拟会产生副作用的外部 MCP 工具。"""
        executions.append(value)
        return "MCP 已完成"

    class Client:
        def __init__(self, servers):
            pass

        async def get_tools(self, *, server_name):
            return [StructuredTool.from_function(coroutine=external_write)]

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible_names={item.name for item in tools})

        def with_structured_output(self, schema, **kwargs):
            return RunnableLambda(lambda messages: {"tools": [external_name]})

        async def _agenerate(self, messages, *, visible_names, **kwargs):
            assert MCP_EXECUTION_GUIDANCE in messages[0].text
            assert FILE_EXECUTION_GUIDANCE in messages[0].text
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            results = [m for m in messages[messages.index(user) + 1:] if isinstance(m, ToolMessage)]
            main = "你是 MelonClaw" in messages[0].text
            file_actions = {
                "写文件": ("write_file", {"file_path": "/result.txt", "content": "原始内容"}),
                "改文件": ("edit_file", {"file_path": "/result.txt", "old_string": "原始内容", "new_string": "修改内容"}),
                "删文件": ("delete", {"file_path": "/result.txt"}),
                "写受保护文件": ("write_file", {"file_path": "/.artifacts/blocked.txt", "content": "拒绝写入"}),
                "改受保护文件": ("edit_file", {"file_path": "/.artifacts/kept.txt", "old_string": "保留内容", "new_string": "拒绝修改"}),
                "删受保护目录": ("delete", {"file_path": "/.artifacts/"}),
            }
            if results and results[-1].name in {external_name, "task", "write_file", "edit_file", "delete"}:
                message = AIMessage(content="完成")
            else:
                if main and delegate:
                    name, args = "task", {"description": user.content, "subagent_type": "general-purpose"}
                elif user.content in file_actions:
                    name, args = file_actions[user.content]
                elif external_name in visible_names:
                    name, args = external_name, {"value": "调用外部工具"}
                else:
                    name, args = "find_tools", {"query": "调用外部 MCP 写入工具"}
                message = AIMessage(content="", tool_calls=[{
                    "name": name, "id": f"{user.id}-{name}", "args": args,
                }])
            return ChatResult(generations=[ChatGeneration(message=message)])

    servers = {"offline": {"transport": "http", "url": "https://offline.invalid/mcp"}}
    external_name = tools_module.wrap_mcp_tool(
        "offline", StructuredTool.from_function(coroutine=external_write), servers["offline"],
    ).name
    monkeypatch.setattr(tools_module, "MultiServerMCPClient", Client)
    monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: Model(responses=[]))

    async def run():
        settings = Settings(workspace_root=tmp_path, data_root=tmp_path / "data", agent_usage_enabled=False)
        resolved = ResolvedModel(profile_id="offline", display_name="offline", source="custom",
            adapter_type="openai", provider="offline", model_name="offline", base_url=None,
            api_key="offline-placeholder")
        workspace = tmp_path / "workspace"
        agent = await build_research_agent(settings, workspace_dir=workspace, model=resolved,
            checkpointer=InMemorySaver(),
            client_capabilities=["user_input_v1"], mcp_servers=servers)
        config = {"configurable": {"thread_id": "mcp-approval-boundary"}}
        result = await agent.ainvoke({"messages": [HumanMessage(id="mcp", content="调用外部工具")]}, config)
        assert result["messages"][-1].content == "完成"
        assert executions == ["调用外部工具"]
        assert not (await agent.aget_state(config)).interrupts

        target = workspace / "result.txt"
        for index, (action, expected) in enumerate([
            ("写文件", "原始内容"), ("改文件", "修改内容"), ("删文件", None),
        ]):
            result = await agent.ainvoke({"messages": [HumanMessage(id=f"file-{index}", content=action)]}, config)
            assert result["messages"][-1].content == "完成"
            assert not (await agent.aget_state(config)).interrupts
            assert (target.read_text() if target.exists() else None) == expected
            if not delegate:
                assert next(m for m in reversed(result["messages"]) if isinstance(m, ToolMessage)).status == "success"

        kept = workspace / ".artifacts" / "kept.txt"
        kept.write_text("保留内容")
        for index, action in enumerate(["写受保护文件", "改受保护文件", "删受保护目录"]):
            result = await agent.ainvoke({"messages": [HumanMessage(id=f"protected-{index}", content=action)]}, config)
            assert result["messages"][-1].content == "完成"
            assert not (await agent.aget_state(config)).interrupts
            assert kept.read_text() == "保留内容"
            assert not (workspace / ".artifacts" / "blocked.txt").exists()
            if not delegate:
                assert next(m for m in reversed(result["messages"]) if isinstance(m, ToolMessage)).status == "error"

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
