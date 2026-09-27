"""供应商高级配置的输入校验；错误中不回显配置值。"""

import json
import re
from typing import Any


class ModelConfigError(ValueError):
    """自定义模型配置不合法。"""

    status_code = 422


def validate_provider_advanced(
    api_key_env: str | None,
    request_headers: dict[str, str] | None,
    extra_config: dict[str, Any] | None,
) -> None:
    if api_key_env and not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:_API_KEY|_ACCESS_TOKEN)", api_key_env):
        raise ModelConfigError("API Key Env 必须是以 _API_KEY 或 _ACCESS_TOKEN 结尾的大写变量名。")
    if request_headers is not None:
        forbidden = {
            "authorization",
            "proxy-authorization",
            "host",
            "content-length",
            "transfer-encoding",
            "cookie",
            "content-type",
            "accept",
        }
        for key, value in request_headers.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", key)
                or key.lower() in forbidden
                or not isinstance(value, str)
                or any(ord(c) < 32 or ord(c) > 126 for c in value)
            ):
                raise ModelConfigError("请求头名称或值不合法，不能覆盖认证和传输控制头。")
        if len({key.lower() for key in request_headers}) != len(request_headers):
            raise ModelConfigError("请求头名称不能重复（不区分大小写）。")
        if len(json.dumps(request_headers)) > 16384:
            raise ModelConfigError("请求头 JSON 过大。")
    if extra_config is not None:
        # 供应商扩展请求体，不允许替换会话、模型或工具协议。
        reserved = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "stream",
            "stream_options",
            "functions",
            "function_call",
            "input",
            "api_key",
            "base_url",
            "headers",
            "default_headers",
        }
        if reserved.intersection(extra_config):
            raise ModelConfigError("扩展配置不能覆盖模型、消息、工具、流式协议或凭据。")

        def check_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if re.search(
                        r"api.?key|secret|password|authorization|credential|access.?token",
                        key,
                        re.I,
                    ):
                        raise ModelConfigError(
                            "扩展配置不得包含凭据字段，请使用 API Key 或请求头。"
                        )
                    check_keys(item)
            elif isinstance(value, list):
                for item in value:
                    check_keys(item)

        check_keys(extra_config)
        try:
            encoded = json.dumps(extra_config, allow_nan=False)
        except (ValueError, TypeError):
            raise ModelConfigError("扩展配置必须是有效 JSON 对象。") from None
        if len(encoded) > 16384:
            raise ModelConfigError("扩展配置 JSON 过大。")
