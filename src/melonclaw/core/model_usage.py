"""每次执行独立的用量账本，覆盖内部模型和子图，绝不保存请求正文。"""

from collections.abc import Callable
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages.utils import count_tokens_approximately


class ModelUsageCallback(AsyncCallbackHandler):
    run_inline = True

    def __init__(self, emit: Callable[[dict], None], *, model_id: str = ""):
        self.emit = emit
        self.model_id = model_id
        self.pending: dict[UUID, dict] = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        meta = metadata or {}
        kind = ("selection" if meta.get("tool_selector") else "summary" if meta.get("lc_source") == "summarization"
                else "subagent" if "|" in str(meta.get("langgraph_checkpoint_ns", "")) else "main")
        params = kwargs.get("invocation_params") or {}
        event = {"type": "model_usage", "call_id": str(run_id), "kind": kind,
                 "model_id": self.model_id, "status": "started",
                 "estimated_input_tokens": sum(int(count_tokens_approximately(batch, tools=params.get("tools"))) for batch in messages),
                 "input_tokens": None, "output_tokens": None, "cache_read_tokens": None}
        self.pending[run_id] = event
        self.emit(dict(event))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        event = self.pending.pop(run_id, None)
        if event is None:
            return
        usage = None
        for batch in response.generations:
            for generation in batch:
                candidate = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if candidate:
                    usage = candidate
                    break
            if usage:
                break
        if usage:
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and value >= 0:
                    event[key] = value
            value = usage.get("input_token_details", {}).get("cache_read")
            if isinstance(value, int) and value >= 0:
                event["cache_read_tokens"] = value
        event["status"] = "completed"
        self.emit(event)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        event = self.pending.pop(run_id, None)
        if event is not None:
            event["status"] = "failed"
            self.emit(event)
