"""创建可注入 Deep Agents 的 LangChain ChatModel。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from melonclaw.core.model_catalog import ResolvedModel


def build_chat_model(model: ResolvedModel) -> ChatOpenAI:
    """按一次运行解析出的模型配置创建 ChatModel，不打印凭据。"""

    if model.adapter_type != "openai_compatible":
        raise ValueError(
            f"没有为 adapter_type={model.adapter_type!r} 配置模型工厂。"
        )

    if model.provider not in {"deepseek", "minimax", "openai"}:
        raise ValueError(f"没有为 provider={model.provider!r} 配置模型工厂。")

    # DeepSeek、MiniMax 和 OpenAI 均通过 OpenAI 兼容 ChatModel 接入；实际
    # Base URL 和 Key 已在 ResolvedModel 中按系统模型槽位解析完成。
    return ChatOpenAI(
        model=model.model_name,
        api_key=model.api_key,
        base_url=model.base_url,
        temperature=0,
    )
