"""创建可注入 Deep Agents 的 LangChain ChatModel。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import Field

from melonclaw.core.model_catalog import ResolvedModel


class ModelInvocationError(RuntimeError):
    """模型调用失败；保留 cause 供执行层分类，不改变重试策略。"""


class ProviderChatOpenAI(ChatOpenAI):
    """供应商请求头按凭据处理，禁止进入模型 repr 和 LangChain 序列化。"""

    default_headers: Mapping[str, str] | None = Field(default=None, repr=False)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            raise ModelInvocationError(str(exc)) from exc

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        except Exception as exc:
            raise ModelInvocationError(str(exc)) from exc

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {**super().lc_secrets, "default_headers": "MELONCLAW_PROVIDER_HEADERS"}


def build_chat_model(model: ResolvedModel) -> ChatOpenAI:
    """按一次运行解析出的模型配置创建 ChatModel，不打印凭据。"""

    # adapter_type 是唯一的工厂分派键：模型目录里的行（不论平台种子还是用户
    # 自建）都是 OpenAI 兼容，provider 只是展示标签。Base URL 和 Key 已在
    # ResolvedModel 中解析完成。
    if model.adapter_type != "openai_compatible":
        raise ValueError(
            f"没有为 adapter_type={model.adapter_type!r} 配置模型工厂。"
        )

    chat_model = ProviderChatOpenAI(
        model=model.model_name,
        api_key=model.api_key,
        base_url=model.base_url,
        temperature=0,
        default_headers=model.request_headers,
        extra_body=model.extra_config,
    )
    profile = {
        "image_inputs": "image" in model.input_modalities,
        "pdf_inputs": "file" in model.input_modalities,
    }
    return chat_model.model_copy(update={"profile": profile})
