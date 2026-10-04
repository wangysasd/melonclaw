"""创建可注入 Deep Agents 的 LangChain ChatModel。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI
from pydantic import Field

from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.core.reasoning import ThinkTagParser, protocol_options, reasoning_blocks


class ModelInvocationError(RuntimeError):
    """模型调用失败；保留 cause 供执行层分类，不改变重试策略。"""


class ProviderChatOpenAI(ChatOpenAI):
    """供应商请求头按凭据处理，禁止进入模型 repr 和 LangChain 序列化。"""

    default_headers: Mapping[str, str] | None = Field(default=None, repr=False)
    reasoning_format: str = "openai"

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is not None and self.reasoning_format in {"reasoning_content", "reasoning_details"}:
            choices = chunk.get("choices", [])
            raw = choices[0].get("delta") or {} if choices else {}
            generation.message.content = reasoning_blocks(raw, self.reasoning_format)
            field = self.reasoning_format
            if field in raw:
                if field == "reasoning_details":
                    # LangChain 按 index 合并字典会拼接 format / signature 等字段。
                    # 无 index 的包只按顺序追加，回传时展开原始供应商片段。
                    generation.message.additional_kwargs["melonclaw_reasoning_details"] = [{"items": raw[field]}]
                else:
                    generation.message.additional_kwargs[field] = raw[field]
        return generation

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        if self.reasoning_format == "openai":
            return result
        raw = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, raw["choices"], strict=True):
            message = generation.message
            row = choice["message"]
            if self.reasoning_format == "think_tags":
                message.additional_kwargs["melonclaw_raw_content"] = row.get("content") or ""
                message.content = ThinkTagParser().feed(row.get("content") or "", final=True)
            else:
                message.content = reasoning_blocks(row, self.reasoning_format)
                if self.reasoning_format in row:
                    message.additional_kwargs[self.reasoning_format] = row[self.reasoning_format]
            message.response_metadata["output_version"] = "v1"
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        if self.reasoning_format == "openai":
            return super()._get_request_payload(input_, stop=stop, **kwargs)
        outbound = []
        for message in messages:
            if isinstance(message, AIMessage):
                if self.reasoning_format == "think_tags":
                    content = message.additional_kwargs.get("melonclaw_raw_content", message.content)
                else:
                    content = message.content if isinstance(message.content, str) else "".join(
                        block["text"] for block in message.content if block.get("type") == "text"
                    )
                message = message.model_copy(update={"content": content})
            outbound.append(message)
        payload = super()._get_request_payload(outbound, stop=stop, **kwargs)
        for original, wire in zip(messages, payload.get("messages", []), strict=True):
            if isinstance(original, AIMessage) and self.reasoning_format in {"reasoning_content", "reasoning_details"}:
                value = original.additional_kwargs.get(self.reasoning_format)
                if self.reasoning_format == "reasoning_details" and "melonclaw_reasoning_details" in original.additional_kwargs:
                    value = [item for packet in original.additional_kwargs["melonclaw_reasoning_details"] for item in packet["items"]]
                if value is not None:
                    wire[self.reasoning_format] = value
        return payload

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            raise ModelInvocationError(str(exc)) from exc

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        parser = ThinkTagParser()
        ordinal = -1
        last_kind = None
        last = None
        try:
            async for chunk in super()._astream(*args, **kwargs):
                if self.reasoning_format != "openai" and isinstance(chunk.message, AIMessageChunk):
                    if self.reasoning_format == "think_tags":
                        raw = chunk.message.content
                        chunk.message.additional_kwargs["melonclaw_raw_content"] = raw
                        blocks = parser.feed(raw) if isinstance(raw, str) else raw
                    else:
                        blocks = chunk.message.content if isinstance(chunk.message.content, list) else []
                    for block in blocks:
                        if block["type"] != last_kind:
                            ordinal += 1
                            last_kind = block["type"]
                        block["index"] = ordinal
                    blocks.extend({**call, "index": 10000 + call["index"], "type": "tool_call_chunk"}
                                  for call in chunk.message.tool_call_chunks)
                    chunk.message.content = blocks
                    chunk.message.response_metadata["output_version"] = "v1"
                    last = chunk
                yield chunk
            if self.reasoning_format == "think_tags" and last is not None:
                tail = parser.feed("", final=True)
                if tail:
                    for block in tail:
                        if block["type"] != last_kind:
                            ordinal += 1
                            last_kind = block["type"]
                        block["index"] = ordinal
                    yield last.model_copy(update={"message": AIMessageChunk(content=tail, response_metadata={"output_version": "v1"})})
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

    reasoning_format, extra_body = protocol_options(model.extra_config)
    chat_model = ProviderChatOpenAI(
        model=model.model_name,
        api_key=model.api_key,
        base_url=model.base_url,
        temperature=0,
        default_headers=model.request_headers,
        extra_body=extra_body,
        reasoning_format=reasoning_format,
    )
    profile = {
        "image_inputs": "image" in model.input_modalities,
        "pdf_inputs": "file" in model.input_modalities,
    }
    return chat_model.model_copy(update={"profile": profile})
