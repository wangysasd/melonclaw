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
