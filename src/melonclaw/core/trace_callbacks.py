"""保留官方回调层级和用量解析，仅在错误进入 SDK 前移除敏感文本。"""

from langfuse.langchain import CallbackHandler
from langgraph.errors import GraphInterrupt

from melonclaw.core.trace_privacy import redact_text


def _safe_error(error, capture_content):
    # Interrupt is a framework control signal; its identity is used by the SDK.
    if isinstance(error, GraphInterrupt):
        return error
    return RuntimeError(redact_text(str(error)) if capture_content else type(error).__name__)


class TraceCallbackHandler(CallbackHandler):
    def __init__(self, *, public_key, capture_content=True):
        super().__init__(public_key=public_key)
        self.capture_content = capture_content

    def on_chain_start(self, serialized, inputs, **kwargs):
        # Keep the runtime/tool name unchanged; give the known subagent a clear
        # telemetry name so the official handler types it as an agent.
        name = kwargs.get("name", "")
        if name == "general-purpose":
            kwargs["name"] = "general-purpose-agent"
        elif name.endswith((".before_agent", ".after_agent")):
            # Lifecycle middleware is a chain, not an independent agent.
            kwargs["name"] = name.removesuffix("_agent")
        return super().on_chain_start(serialized, inputs, **kwargs)

    def on_chain_error(self, error, **kwargs):
        return super().on_chain_error(_safe_error(error, self.capture_content), **kwargs)

    def on_llm_error(self, error, **kwargs):
        return super().on_llm_error(_safe_error(error, self.capture_content), **kwargs)

    def on_tool_error(self, error, **kwargs):
        return super().on_tool_error(_safe_error(error, self.capture_content), **kwargs)

    def on_retriever_error(self, error, **kwargs):
        return super().on_retriever_error(_safe_error(error, self.capture_content), **kwargs)
