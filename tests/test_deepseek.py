"""联网工具协议、受控重试、并发节流与取消的回归。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from melonclaw.core.task_policy import TaskExecutionError
from melonclaw.tool import deepseek
from melonclaw.tool.search import build_web_tools
from melonclaw.tool.task_search import build_task_search_tool


@pytest.mark.parametrize('payload', [
    {'query': 'x', 'count': True}, {'query': 'x', 'count': '2'},
    {'query': 'x', 'count': 0}, {'query': 'x', 'count': 21},
    {'query': 'x', 'need_content': 'false'}, {'query': ' '},
    {'query': 'x' * 129}, {'query': 'x', 'ori_question': ' '},
])
def test_bound_tool_strict_input(payload):
    with pytest.raises(ValidationError):
        build_web_tools('fixture')[0].invoke(payload)


@pytest.mark.parametrize('key', [None, '', '  '])
def test_empty_bound_key_cannot_read_environment(monkeypatch, key):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-used')
    for tool, args in zip(build_web_tools(key), [
        {'query': 'test'}, {'url': 'https://example.org'},
    ], strict=True):
        with pytest.raises(ValueError, match='未配置'):
            tool.invoke(args)


@pytest.mark.parametrize('data', [None, [], {}, {'results': None},
    {'results': {}}, {'results': [None]},
    {'results': [{'title': 'x', 'url': 'https://example.org'}]},
    {'results': [{'title': 'x', 'url': 'x', 'summary': 2}]},
])
def test_invalid_search_response_is_not_empty_success(data):
    with pytest.raises(deepseek.DeepSeekResponseError, match='响应格式异常'):
        deepseek.validate_response(data)


@pytest.mark.parametrize('data', [{}, {'title': 'x'},
    {'title': 'x', 'content': ''}, {'title': 'x', 'content': '  '},
    {'title': 'x', 'content': 123},
])
def test_invalid_fetch_response(data):
    with pytest.raises(deepseek.DeepSeekResponseError):
        deepseek.validate_response(data, fetch=True)


def test_valid_empty_search():
    assert deepseek.validate_response({'results': []}) == {'results': []}


@pytest.fixture
def network(monkeypatch):
    """真实 httpx 序列化和错误类型，只替换网络及等待。"""
    sync_client, async_client = httpx.Client, httpx.AsyncClient
    calls, sleeps = [], []
    state = {'responses': []}

    def handle(request):
        calls.append(request)
        response = state['responses'].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(deepseek.httpx, 'Client', lambda **kw: sync_client(transport=transport, **kw))
    monkeypatch.setattr(deepseek.httpx, 'AsyncClient', lambda **kw: async_client(transport=transport, **kw))
    monkeypatch.setattr(deepseek, 'REQUEST_INTERVAL_SECONDS', 0)
    monkeypatch.setattr(deepseek, '_PACER', deepseek.RequestPacer())
    monkeypatch.setattr(deepseek, 'time', SimpleNamespace(monotonic=lambda: 100.0, sleep=sleeps.append))

    async def sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr(deepseek, 'asyncio', SimpleNamespace(sleep=sleep))
    return state, calls, sleeps


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('status', [408, 429, 500, 503, 504])
def test_transient_failure_retries_then_succeeds(network, mode, status):
    state, calls, sleeps = network
    state['responses'] = [httpx.Response(status), httpx.Response(200, json={'results': []})]
    if mode == 'sync':
        result = deepseek.request_json(deepseek.DEEPSEEK_SEARCH_URL, {}, 'fixture')
    else:
        result = asyncio.run(deepseek.request_json_async(deepseek.DEEPSEEK_SEARCH_URL, {}, 'fixture'))
    assert result == {'results': []}
    assert len(calls) == 2 and sleeps == [0.5]


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('status', [400, 401, 402, 403, 404])
def test_permanent_errors_do_not_retry(network, mode, status):
    state, calls, sleeps = network
    state['responses'] = [httpx.Response(status, text='secret-provider-body')]
    with pytest.raises((RuntimeError, TaskExecutionError)) as caught:
        if mode == 'sync':
            build_web_tools('fixture')[0].invoke({'query': 'x'})
        else:
            asyncio.run(build_task_search_tool('fixture').ainvoke({'query': 'x'}))
    assert len(calls) == 1 and not sleeps
    assert 'secret-provider-body' not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_retry_exhaustion_is_bounded(network, mode):
    state, calls, sleeps = network
    state['responses'] = [httpx.ReadTimeout('private-key')] * 3
    with pytest.raises((RuntimeError, TaskExecutionError)) as caught:
        if mode == 'sync':
            build_web_tools('fixture')[0].invoke({'query': 'x'})
        else:
            asyncio.run(build_task_search_tool('fixture').ainvoke({'query': 'x'}))
    assert len(calls) == 3 and sleeps == [0.5, 1.0]
    assert 'private-key' not in str(caught.value)


def test_retry_after_respected(network):
    state, calls, sleeps = network
    state['responses'] = [httpx.Response(429, headers={'Retry-After': '2'}),
                          httpx.Response(200, json={})]
    deepseek.request_json(deepseek.DEEPSEEK_SEARCH_URL, {}, 'fixture')
    assert len(calls) == 2 and sleeps == [2.0]


def test_long_retry_after_is_not_retried_early(network):
    state, calls, sleeps = network
    state['responses'] = [httpx.Response(429, headers={'Retry-After': '60'})]
    with pytest.raises(httpx.HTTPStatusError):
        deepseek.request_json(deepseek.DEEPSEEK_SEARCH_URL, {}, 'fixture')
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_invalid_json_not_retried_and_redacted(network, mode):
    state, calls, sleeps = network
    state['responses'] = [httpx.Response(200, text='not-json-private')]
    with pytest.raises((RuntimeError, TaskExecutionError)) as caught:
        if mode == 'sync':
            build_web_tools('fixture')[1].invoke({'url': 'https://example.org'})
        else:
            asyncio.run(build_task_search_tool('fixture').ainvoke({'query': 'x'}))
    assert len(calls) == 1 and not sleeps
    assert 'not-json-private' not in str(caught.value)


def test_truncation_and_intact_source_url(network):
    state, _, _ = network
    source = 'https://example.org/' + 'a' * 2500
    state['responses'] = [httpx.Response(200, json={'results': [{
        'title': 'x', 'url': source, 'summary': 's' * 2100, 'content': 'c' * 8100,
    }]})]
    result = build_web_tools('fixture')[0].invoke({'query': 'x', 'need_content': True})['results'][0]
    assert result['truncated_fields'] == ['summary', 'content']
    assert len(result['summary']) == 2000 and len(result['content']) == 8000
    assert result['url'] == source


def test_pacer_serializes_concurrent_callers_and_separates_endpoints(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(deepseek, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    pacer = deepseek.RequestPacer()
    with ThreadPoolExecutor(max_workers=8) as pool:
        delays = list(pool.map(lambda _: pacer.delay('search'), range(20)))
    assert delays.count(0.0) == 1
    assert pacer.delay('fetch') == 0
    now[0] += 0.21
    assert pacer.delay('search') == 0


def test_async_pacer_cancellation_does_not_hold_lock():
    async def run():
        pacer = deepseek.RequestPacer()
        await pacer.await_slot('search')
        pending = asyncio.create_task(pacer.await_slot('search'))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert pacer.delay('fetch') == 0
    asyncio.run(run())


def test_cancellation_during_backoff_propagates(network, monkeypatch):
    state, calls, _ = network
    state['responses'] = [httpx.Response(503)]

    async def cancel(delay):
        raise asyncio.CancelledError
    monkeypatch.setattr(deepseek.asyncio, 'sleep', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(build_task_search_tool('fixture').ainvoke({'query': 'x'}))
    assert len(calls) == 1


@pytest.mark.parametrize('header,expected', [
    ('Sat, 17 Oct 2026 00:00:03 GMT', 3.0),
    ('Sat, 17 Oct 2026 00:01:00 GMT', None),
    ('garbage', None), ('NaN', None), ('-2', None),
])
def test_retry_after_date_and_invalid_values(monkeypatch, header, expected):
    from datetime import datetime, timezone

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 17, tzinfo=timezone.utc)
    monkeypatch.setattr(deepseek, 'datetime', Clock)
    request = httpx.Request('POST', deepseek.DEEPSEEK_SEARCH_URL)
    response = httpx.Response(429, request=request, headers={'Retry-After': header})
    error = httpx.HTTPStatusError('limited', request=request, response=response)
    assert deepseek.retry_delay(error, 0) == expected


def test_schema_errors_reach_both_tool_paths(network):
    state, calls, _ = network
    state['responses'] = [httpx.Response(200, json={'unexpected': 'private'}),
                          httpx.Response(200, json={'unexpected': 'private'})]
    with pytest.raises(RuntimeError, match='响应格式异常'):
        build_web_tools('fixture')[0].invoke({'query': 'x'})
    with pytest.raises(TaskExecutionError, match='响应格式异常'):
        asyncio.run(build_task_search_tool('fixture').ainvoke({'query': 'x'}))
    assert len(calls) == 2
