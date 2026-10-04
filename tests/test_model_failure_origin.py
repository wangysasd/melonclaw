"""模型边界应保留错误归属，取消不能被转换成模型失败。"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_openai import ChatOpenAI

from melonclaw.core.chat_model import ModelInvocationError, ProviderChatOpenAI


def test_model_failure_has_explicit_origin(monkeypatch):
    cause = ValueError("invalid provider response")
    monkeypatch.setattr(ChatOpenAI, "_agenerate", AsyncMock(side_effect=cause))
    model = ProviderChatOpenAI(model="test", api_key="test-placeholder")
    with pytest.raises(ModelInvocationError) as caught:
        asyncio.run(model._agenerate([]))
    assert caught.value.__cause__ is cause


def test_model_cancel_is_not_failure(monkeypatch):
    monkeypatch.setattr(ChatOpenAI, "_agenerate", AsyncMock(side_effect=asyncio.CancelledError))
    model = ProviderChatOpenAI(model="test", api_key="test-placeholder")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model._agenerate([]))


def test_context_overflow_remains_visible_to_official_summary(monkeypatch):
    from langchain_core.exceptions import ContextOverflowError
    error = ContextOverflowError('too long')
    monkeypatch.setattr(ChatOpenAI, '_agenerate', AsyncMock(side_effect=error))
    model = ProviderChatOpenAI(model='test', api_key='test-placeholder')
    with pytest.raises(ContextOverflowError):
        asyncio.run(model._agenerate([]))


def test_stream_failure_marks_partial_output_and_does_not_expose_provider_error(monkeypatch):
    import httpx
    import openai
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    from melonclaw.core.agent_errors import retry_transient_model_error

    async def fail(self, *args, **kwargs):
        yield ChatGenerationChunk(message=AIMessageChunk(content='visible'))
        raise openai.APIConnectionError(message='sensitive-provider-response', request=httpx.Request('POST', 'https://offline.invalid'))

    monkeypatch.setattr(ChatOpenAI, '_astream', fail)
    async def run():
        model = ProviderChatOpenAI(model='test', api_key='test-placeholder')
        with pytest.raises(ModelInvocationError) as caught:
            async for _ in model._astream([]):
                pass
        assert caught.value.partial_output
        assert not retry_transient_model_error(caught.value)
        assert 'sensitive-provider-response' not in str(caught.value)
    asyncio.run(run())
