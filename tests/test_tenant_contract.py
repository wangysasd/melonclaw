"""租户归属只从服务端用户记录解析。"""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute
from pydantic import BaseModel

from melonclaw.api import schemas
from melonclaw.api.app import app


def test_api_does_not_declare_client_tenant_id():
    models = (
        model
        for _, model in inspect.getmembers(schemas, inspect.isclass)
        if issubclass(model, BaseModel) and model.__module__ == schemas.__name__
    )
    for model in models:
        assert "tenant_id" not in model.model_fields, model.__name__

    for route in app.routes:
        if isinstance(route, APIRoute):
            assert "tenant_id" not in inspect.signature(route.endpoint).parameters, (
                route.path
            )
