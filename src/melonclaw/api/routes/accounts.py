"""登录与系统管理接口。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from melonclaw.api.auth import accounts, bearer_token, public_user
from melonclaw.core.config import load_settings

router = APIRouter()
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    user_id: Identifier
    password: SecretStr = Field(min_length=5, max_length=128)


class Switch(Input):
    user_id: Identifier


class UserEdit(Input):
    user_name_zh: Name
    tenant_id: Identifier


class Password(Input):
    password: SecretStr = Field(min_length=5, max_length=128)
    confirm_password: SecretStr = Field(min_length=5, max_length=128)

    def confirmed_password(self) -> str:
        password = self.password.get_secret_value()
        if password != self.confirm_password.get_secret_value():
            raise HTTPException(422, "两次输入的密码不一致。")
        return password


class UserCreate(UserEdit, Password):
    user_id: Identifier


class TenantEdit(Input):
    tenant_name_zh: Name
    enabled: bool = True


class TenantCreate(TenantEdit):
    tenant_id: Identifier


def _token_response(response: Response, token: str):
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token}


@router.get("/api/auth/config")
async def config(request: Request):
    settings = load_settings()
    return {"passwordless": settings.profile == "dev", "assistant_name": settings.assistant_name}


@router.post("/api/auth/login")
async def login(request: Request, response: Response, body: Login):
    request.state.audit_account = body.user_id
    try:
        token = await accounts(request).login(body.user_id, body.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from None
    return _token_response(response, token)


@router.post("/api/auth/passwordless")
async def passwordless(request: Request, response: Response):
    request.state.audit_account = "admin"
    if load_settings().profile != "dev":
        raise HTTPException(403, "免密登录未启用。")
    token = await accounts(request).login("admin", "", passwordless=True)
    return _token_response(response, token)


@router.get("/api/auth/session")
async def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return public_user(request.state.user)


@router.post("/api/auth/switch")
async def switch(request: Request, body: Switch):
    request.state.audit_target = body.user_id
    if load_settings().profile != "dev":
        raise HTTPException(403, "切换用户未启用。")
    try:
        token = await accounts(request).switch(bearer_token(request), body.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"access_token": token}


@router.post("/api/auth/password")
async def own_password(request: Request, body: Password):
    await accounts(request).reset_password(request.state.user.user_id, body.confirmed_password())
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout():
    return {"ok": True}


@router.get("/api/admin/users")
async def users(request: Request):
    return {"items": await accounts(request).storage.account_users()}


@router.post("/api/admin/users", status_code=201)
async def create_user(request: Request, body: UserCreate):
    await accounts(request).save_user(body.user_id, body.user_name_zh, body.tenant_id,
                                     body.confirmed_password())
    return {"ok": True}


@router.patch("/api/admin/users/{user_id}")
async def edit_user(request: Request, user_id: Identifier, body: UserEdit):
    await accounts(request).save_user(user_id, body.user_name_zh, body.tenant_id)
    return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(request: Request, user_id: Identifier):
    await accounts(request).delete_user(user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/password")
async def password(request: Request, user_id: Identifier, body: Password):
    await accounts(request).reset_password(user_id, body.confirmed_password())
    return {"ok": True}


@router.get("/api/admin/tenants")
async def tenants(request: Request):
    return {"items": await accounts(request).storage.account_tenants()}


@router.post("/api/admin/tenants", status_code=201)
async def create_tenant(request: Request, body: TenantCreate):
    await accounts(request).save_tenant(body.tenant_id, body.tenant_name_zh, body.enabled, create=True)
    return {"ok": True}


@router.patch("/api/admin/tenants/{tenant_id}")
async def edit_tenant(request: Request, tenant_id: Identifier, body: TenantEdit):
    await accounts(request).save_tenant(tenant_id, body.tenant_name_zh, body.enabled)
    return {"ok": True}
