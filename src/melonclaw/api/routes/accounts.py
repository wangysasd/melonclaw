"""登录与系统管理接口。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from melonclaw.api.auth import COOKIE, accounts, public_user
from melonclaw.core.config import load_settings
from melonclaw.services.accounts import token_digest

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


def set_cookie(request, response, token):
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=7 * 86400, path="/")
    response.headers["Cache-Control"] = "no-store"


@router.get("/api/auth/config")
async def config(request: Request):
    return {"passwordless": load_settings().profile == "dev"}


@router.post("/api/auth/login")
async def login(request: Request, response: Response, body: Login):
    try:
        token = await accounts(request).login(body.user_id, body.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from None
    set_cookie(request, response, token)
    return {"ok": True}


@router.post("/api/auth/passwordless")
async def passwordless(request: Request, response: Response):
    if load_settings().profile != "dev":
        raise HTTPException(403, "免密登录未启用。")
    token = await accounts(request).login("admin", "", passwordless=True)
    set_cookie(request, response, token)
    return {"ok": True}


@router.get("/api/auth/session")
async def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return public_user(request.state.user)


@router.post("/api/auth/switch")
async def switch(request: Request, body: Switch):
    if load_settings().profile != "dev":
        raise HTTPException(403, "切换用户未启用。")
    await accounts(request).switch(request.cookies[COOKIE], body.user_id)
    return {"ok": True}


@router.post("/api/auth/password")
async def own_password(request: Request, response: Response, body: Password):
    await accounts(request).reset_password(request.state.user.user_id, body.confirmed_password())
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    await accounts(request).storage.delete_auth_session(token_digest(request.cookies[COOKIE]))
    response.delete_cookie(COOKIE, path="/")
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
