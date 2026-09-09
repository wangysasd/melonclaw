"""创建可注入 Deep Agents 的 LangChain ChatModel。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from melonclaw.core.config import Settings


def build_chat_model(settings: Settings) -> ChatOpenAI:
    """按项目配置创建模型实例，不在这里打印任何凭据。"""

    settings.validate()

    if settings.provider in {"deepseek", "openai"}:
        # DeepSeek 也使用 OpenAI 兼容接口；默认通过 DEEPSEEK_BASE_URL 访问 DeepSeek 的 OpenAI API。
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
        )

    raise ValueError(f"没有为 provider={settings.provider!r} 配置模型工厂。")
