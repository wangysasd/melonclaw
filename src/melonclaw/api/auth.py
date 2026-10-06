"""统一 Cookie 身份边界，覆盖 JSON、上传、下载和 SSE。"""
from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from melonclaw.api.dependencies import get_chat_service
from melonclaw.services.accounts import AccountService

COOKIE = "melonclaw_session"
PUBLIC_PATHS = {"/api/auth/config", "/api/auth/login", "/api/auth/passwordless", "/api/ready"}


def accounts(request: Request) -> AccountService:
    manager = get_chat_service(request)
    if manager.storage is None:
        raise HTTPException(503, "账户服务尚未就绪，请稍后重试。")
    return AccountService(manager.storage)


def public_user(context):
    return asdict(context)


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
    check_origin(request)
    if request.url.path in PUBLIC_PATHS:
        yield
        return
    service = accounts(request)
    # 管理变更自己获取独占锁，不能与本请求共享锁互相等待。
    if request.url.path.startswith(("/api/admin/", "/api/auth/")):
        await authenticate(request, service)
        yield
    else:
        async with service.storage.account_guard():
            await authenticate(request, service)
            yield


async def authenticate(request, service):
    context = await service.session(request.cookies.get(COOKIE, ""))
    if context is None:
        raise HTTPException(401, "请先登录。")
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
    # 请求 user_id 仅作页面上下文断言，绝不能覆盖 Cookie 身份。
    provided = list(request.query_params.getlist("user_id"))
    content_type = request.headers.get("content-type", "")
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
    if any(value != context.user_id for value in provided):
        raise HTTPException(409, "当前用户已变化，请刷新页面。")
