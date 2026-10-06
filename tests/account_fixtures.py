"""非认证路由的单元测试显式注入已验证身份；认证集成测试不使用此替身。"""
from types import SimpleNamespace

from fastapi import Request

from melonclaw.api.auth import require_session


def authenticated_route_app(app, user_id="owner"):
    async def identity(request: Request):
        request.state.user = SimpleNamespace(user_id=user_id, tenant_id="system")
    app.dependency_overrides[require_session] = identity
    return app
