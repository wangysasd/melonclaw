"""非认证路由的单元测试显式注入已验证身份；认证集成测试不使用此替身。"""
from types import SimpleNamespace

from fastapi import Request

from melonclaw.api.auth import require_session


def use_access_token(client, response):
    """把登录或切换响应里的令牌装到后续请求。"""

    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


def authenticated_route_app(app, user_id="owner"):
    async def identity(request: Request):
        request.state.user = SimpleNamespace(user_id=user_id, tenant_id="system")
    app.dependency_overrides[require_session] = identity
    return app
