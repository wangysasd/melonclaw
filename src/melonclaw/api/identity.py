"""附件请求身份解析入口。

当前部署把页面用户当作**开发模拟用户**：``user_id`` 由查询参数或多部分表单显式
提交，真正的业务校验（用户的唯一有效租户归属、Project / Conversation 归属）仍然在
服务层完成。本模块不代表生产认证。

部署方如果已经在受信任网关后面统一注入身份，可以设置
``MELONCLAW_IDENTITY_HEADER``，让该请求头成为 ``user_id`` 的唯一来源；本模块不校验
请求头的签名，因此它只是把“身份从哪里读”从路由参数里解耦出来的接入点。浏览器
``<img>`` 直接请求附件内容时无法附加自定义请求头，未来若要真正收紧附件下载，需要
改为短期签名 URL 或 Cookie，而不是依赖该请求头。
"""

from __future__ import annotations

import os

from fastapi import Request

from melonclaw.services.errors import InvalidUserError

IDENTITY_HEADER_ENV = "MELONCLAW_IDENTITY_HEADER"


def identity_header_name() -> str:
    """返回部署方配置的身份请求头名；未配置时为空串。"""

    return os.getenv(IDENTITY_HEADER_ENV, "").strip()


def resolve_request_user_id(request: Request, provided: str | None) -> str:
    """返回本次请求的 ``user_id``；缺失时抛出可映射为 400 的业务错误。"""

    header_name = identity_header_name()
    if header_name:
        value = (request.headers.get(header_name) or "").strip()
        if not value:
            raise InvalidUserError(f"缺少 {header_name} 请求头。")
        return value
    value = (provided or "").strip()
    if not value:
        raise InvalidUserError("缺少 user_id。")
    return value


__all__ = ["IDENTITY_HEADER_ENV", "identity_header_name", "resolve_request_user_id"]
