"""模型目录与自定义模型管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import (
    ModelConfigCreateRequest,
    ModelConfigUpdateRequest,
    ModelProviderCreateRequest,
    ModelProviderTestRequest,
    ModelProviderUpdateRequest,
    UserProviderKeyRequest,
)
from melonclaw.services.resource_service import ModelConfigPayload, ProviderConfigPayload

router = APIRouter()


@router.get("/api/models")
async def list_models(
    request: Request,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.models(user_id))
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/models/manage")
async def list_manageable_models(
    request: Request,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.manageable_models(user_id))
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.post("/api/models")
async def create_model(
    request: Request,
    body: ModelConfigCreateRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.create_model(
            body.user_id,
            ModelConfigPayload(
                model_key=body.model_key,
                provider_key=body.provider_key,
                scope=body.scope,
                display_name=body.display_name,
                model_name=body.model_name,
                context_window=body.context_window,
                enabled=body.enabled,
            ),
        )
        return JSONResponse({"ok": True}, status_code=201)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.patch("/api/models/{model_key}")
async def update_model(
    request: Request,
    model_key: str,
    body: ModelConfigUpdateRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.update_model(
            body.user_id,
            model_key,
            enabled=body.enabled,
            display_name=body.display_name,
            model_name=body.model_name,
            context_window=body.context_window,
            is_default=body.is_default,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/model-providers")
async def list_manageable_providers(
    request: Request,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.manageable_providers(user_id))
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.post("/api/model-providers/test-connection")
async def test_provider_connection(
    request: Request,
    body: ModelProviderTestRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(
            await manager.test_provider_connection(
                body.user_id,
                provider_key=body.provider_key,
                base_url=body.base_url,
                models_endpoint=body.models_endpoint,
                api_key=body.api_key,
                api_key_env=body.api_key_env,
                request_headers=body.request_headers,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.post("/api/model-providers")
async def create_provider(
    request: Request,
    body: ModelProviderCreateRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.create_provider(
            body.user_id,
            ProviderConfigPayload(
                provider_key=body.provider_key,
                scope=body.scope,
                display_name=body.display_name,
                provider_type=body.provider_type,
                base_url=body.base_url,
                api_key=body.api_key,
                models_endpoint=body.models_endpoint,
                api_key_env=body.api_key_env,
                request_headers=body.request_headers,
                extra_config=body.extra_config,
                enabled=body.enabled,
            ),
        )
        return JSONResponse({"ok": True}, status_code=201)
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.patch("/api/model-providers/{provider_key}")
async def update_provider(
    request: Request,
    provider_key: str,
    body: ModelProviderUpdateRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.update_provider(
            body.user_id,
            provider_key,
            enabled=body.enabled,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=body.api_key,
            models_endpoint=body.models_endpoint,
            api_key_env=body.api_key_env,
            request_headers=body.request_headers,
            extra_config=body.extra_config,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.delete("/api/model-providers/{provider_key}")
async def delete_provider(
    request: Request,
    provider_key: str,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.delete_provider(user_id, provider_key)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.get("/api/model-providers/{provider_key}/remote-models")
async def fetch_provider_remote_models(
    request: Request,
    provider_key: str,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        return JSONResponse(await manager.fetch_remote_models(user_id, provider_key))
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.put("/api/model-providers/{provider_key}/my-key")
async def set_my_provider_key(
    request: Request,
    provider_key: str,
    body: UserProviderKeyRequest,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.set_user_provider_key(
            body.user_id, provider_key, body.api_key
        )
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.delete("/api/model-providers/{provider_key}/my-key")
async def delete_my_provider_key(
    request: Request,
    provider_key: str,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.delete_user_provider_key(user_id, provider_key)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)


@router.delete("/api/models/{model_key}")
async def delete_model(
    request: Request,
    model_key: str,
    user_id: str,
) -> JSONResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        await manager.delete_model(user_id, model_key)
        return JSONResponse({"ok": True})
    except Exception as exc:  # noqa: BLE001 - 统一返回安全错误
        return error_response(exc)
