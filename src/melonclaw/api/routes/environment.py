"""只写环境变量管理 API；客户端不能指定 scope 或 owner。"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.services.environment import EnvironmentService

router = APIRouter()


class EnvironmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    user_id: str
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=8192, repr=False)
    description: str = Field(default="", max_length=500)
    enabled: bool = True


class EnvironmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    user_id: str
    expected_version: int = Field(ge=1)
    value: str | None = Field(default=None, max_length=8192, repr=False)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


def service(request):
    return EnvironmentService(get_chat_service(request).runtime)


async def respond(operation, status=200):
    try:
        return JSONResponse(await operation, status_code=status)
    except Exception as exc:
        if isinstance(exc, ValueError):
            return error_response(exc)
        return JSONResponse({"error": "环境变量操作失败，请稍后重试。"}, status_code=500)


@router.get("/api/environment")
async def list_environment(request: Request, user_id: str):
    return await respond(service(request).list(user_id))


@router.post("/api/environment")
async def create_environment(request: Request, payload: EnvironmentCreate):
    return await respond(
        service(request).save(
            payload.user_id,
            name=payload.name,
            fields=payload.model_dump(exclude={"user_id", "name"}),
        ),
        201,
    )


@router.patch("/api/environment/{identifier}")
async def update_environment(request: Request, identifier: UUID, payload: EnvironmentUpdate):
    return await respond(
        service(request).save(
            payload.user_id,
            identifier=str(identifier),
            expected_version=payload.expected_version,
            fields=payload.model_dump(exclude={"user_id", "expected_version"}, exclude_unset=True),
        )
    )


@router.delete("/api/environment/{identifier}")
async def delete_environment(
    request: Request, identifier: UUID, user_id: str, expected_version: int = Query(ge=1)
):
    return await respond(
        service(request).save(
            user_id, identifier=str(identifier), expected_version=expected_version, delete=True
        )
    )
