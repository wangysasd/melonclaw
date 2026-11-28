"""统一 Bearer 令牌身份边界，覆盖 JSON、上传、下载和 SSE。"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from time import perf_counter
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from melonclaw.api.dependencies import get_chat_service
from melonclaw.services.accounts import AccountService

PUBLIC_PATHS = {
    "/api/auth/config",
    "/api/auth/login",
    "/api/auth/passwordless",
    "/api/auth/sms/challenges",
    "/api/auth/register",
    "/api/auth/login/sms",
    "/api/auth/password/reset",
    "/api/ready",
    "/api/reports/channels",
    "/api/reports/anonymous",
    "/api/reports/lookup",
}


def bearer_token(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def accounts(request: Request) -> AccountService:
    manager = get_chat_service(request)
    if manager.storage is None:
        raise HTTPException(503, "账户服务尚未就绪，请稍后重试。")
    state = request.app.state
    return AccountService(
        manager.storage,
        sms=getattr(state, "sms_provider", None),
        settings=getattr(state, "sms_settings", None),
        clock=getattr(state, "sms_clock", None),
    )


def public_user(context):
    identity = asdict(context)
    del identity["tenant_enabled"]
    return identity


def check_origin(request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站写入。")
    if origin:
        parsed = urlsplit(origin)
        same_origin = parsed.netloc == request.headers.get("host") and parsed.scheme == request.url.scheme
        if not same_origin and origin not in request.app.state.allowed_origins:
            raise HTTPException(403, "请求来源无效，请通过同源或已配置的同站页面访问。")


async def require_session(request: Request):
    request.state.preparation_started_at = perf_counter()
    check_origin(request)
    if request.url.path in PUBLIC_PATHS:
        yield
        return
    # 管理变更自己获取独占锁，不能与本请求共享锁互相等待。
    if request.url.path.startswith(("/api/admin/", "/api/auth/")):
        await authenticate(request, accounts(request))
        yield
    else:
        async with account_session(request):
            yield


@asynccontextmanager
async def account_session(request: Request, *, check_body: bool = True):
    """身份与业务归属校验共用锁，不把外部 I/O 放在这个作用域内。"""
    service = accounts(request)
    async with service.storage.account_guard():
        await authenticate(request, service, check_body=check_body, require_enabled_tenant=True)
        yield


def assert_request_user(provided: Iterable[object], user_id: str) -> None:
    if any(value != user_id for value in provided):
        raise HTTPException(409, "当前用户已变化，请刷新页面。")


async def authenticate(request, service, *, check_body: bool = True, require_enabled_tenant: bool = False):
    context = await service.session(bearer_token(request))
    if context is None:
        raise HTTPException(401, "请先登录。")
    if require_enabled_tenant and not context.tenant_enabled:
        raise HTTPException(403, "当前用户所属租户已停用。")
    request.state.user = context
    expected = request.headers.get("x-melonclaw-user")
    if expected is not None and expected != context.user_id:
        raise HTTPException(409, "当前用户已变化，请刷新页面。")
    if request.url.path.startswith("/api/admin/"):
        if context.user_id != "admin" or context.tenant_id != "system":
            raise HTTPException(403, "只有 admin 可以管理用户和租户。")
        return
    if request.url.path.startswith("/api/auth/"):
        return
    # 请求 user_id 仅作页面上下文断言，绝不能覆盖令牌身份。
    provided = list(request.query_params.getlist("user_id"))
    content_type = request.headers.get("content-type", "") if check_body else ""
    if "application/json" in content_type:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            provided.extend(body[key] for key in ("user_id", "actor_user_id") if key in body)
    elif "multipart/form-data" in content_type:
        form = await request.form()
        provided.extend(form.getlist("user_id"))
    assert_request_user(provided, context.user_id)
