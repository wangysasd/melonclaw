"""真实 Cookie、HTTP 校验与 PostgreSQL 环境资源生命周期；仅使用随机测试 schema。"""

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.api.app import create_app
from melonclaw.core.passwords import hash_password
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import metadata, tenants, users
from melonclaw.repository import BusinessRepository
from melonclaw.repository.mappers import _now

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)


@asynccontextmanager
async def environment_api():
    schema = "test_environment_api_" + uuid4().hex
    engine = create_async_engine(
        normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(metadata.create_all)
            for tenant in ("system", "team-a", "team-b"):
                await conn.execute(
                    insert(tenants).values(
                        tenant_id=tenant, tenant_name_zh=tenant, created_at=_now()
                    )
                )
            for name, tenant, role in (
                ("admin", "system", "admin"),
                ("alice", "team-a", "member"),
                ("bob", "team-b", "member"),
            ):
                await conn.execute(
                    insert(users).values(
                        user_id=name,
                        user_name_zh=name,
                        tenant_id=tenant,
                        tenant_role=role,
                        password_hash=hash_password("test-password"),
                        created_at=_now(),
                    )
                )
        repository = BusinessRepository(SimpleNamespace(engine=engine))
        app = create_app()
        app.state.chat = SimpleNamespace(
            storage=repository, runtime=SimpleNamespace(require_ready=lambda: repository)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            yield client, repository
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def login(client, user_id):
    response = await client.post(
        "/api/auth/login", json={"user_id": user_id, "password": "test-password"}
    )
    assert response.status_code == 200


def test_http_personal_override_fallback_and_write_only_lifecycle():
    async def run():
        async with environment_api() as (client, repository):
            await login(client, "admin")
            response = await client.post(
                "/api/environment",
                json={"user_id": "admin", "name": "DEMO_TOKEN", "value": "shared-fixture"},
            )
            assert response.status_code == 201 and "shared-fixture" not in response.text
            shared = response.json()
            assert shared["scope"] == "global"
            metadata_response = await client.get("/api/environment?user_id=admin")
            assert "shared-fixture" not in metadata_response.text
            item = metadata_response.json()["items"][0]
            assert item["masked_value"] == "shar***ture" and "value" not in item

            await login(client, "alice")
            assert (await client.get("/api/environment?user_id=alice")).json() == {
                "scope": "user",
                "items": [],
            }
            response = await client.post(
                "/api/environment", json={"user_id": "alice", "name": "DEMO_TOKEN", "value": ""}
            )
            assert response.status_code == 201
            personal = response.json()
            assert personal["scope"] == "user"
            assert await repository.effective_environment("alice") == {"DEMO_TOKEN": ""}
            assert await repository.effective_environment("bob") == {"DEMO_TOKEN": "shared-fixture"}
            path = f"/api/environment/{personal['id']}"
            response = await client.patch(
                path, json={"user_id": "alice", "expected_version": 1, "description": "新用途"}
            )
            assert response.status_code == 200 and response.json()["version"] == 2
            assert await repository.effective_environment("alice") == {"DEMO_TOKEN": ""}
            response = await client.patch(
                path, json={"user_id": "alice", "expected_version": 2, "enabled": False}
            )
            assert response.status_code == 200
            assert await repository.effective_environment("alice") == {
                "DEMO_TOKEN": "shared-fixture"
            }
            response = await client.patch(
                path, json={"user_id": "alice", "expected_version": 3, "enabled": True}
            )
            assert response.status_code == 200
            assert await repository.effective_environment("alice") == {"DEMO_TOKEN": ""}

            await login(client, "bob")
            response = await client.delete(path, params={"user_id": "bob", "expected_version": 4})
            assert response.status_code == 404
            await login(client, "alice")
            response = await client.delete(path, params={"user_id": "alice", "expected_version": 3})
            assert response.status_code == 409
            response = await client.delete(path, params={"user_id": "alice", "expected_version": 4})
            assert response.status_code == 200
            assert await repository.effective_environment("alice") == {
                "DEMO_TOKEN": "shared-fixture"
            }
            response = await client.patch(
                f"/api/environment/{shared['id']}",
                json={"user_id": "alice", "expected_version": 1, "enabled": False},
            )
            assert response.status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields",
    [
        {"scope": "global"},
        {"owner_user_id": "admin"},
        {"enabled": "true"},
        {"value": None},
        {"name": "HOME"},
        {"value": "x" * 8193},
        {"value": "界" * 2731},
        {"value": "fixture-sensitive\u0000"},
        {"value": "fixture-sensitive\ud800"},
        {"description": "fixture-sensitive\ud800"},
    ],
    ids=[
        "scope",
        "owner",
        "bool",
        "null",
        "reserved",
        "chars",
        "bytes",
        "nul",
        "utf8",
        "description-utf8",
    ],
)
def test_http_rejects_invalid_or_privileged_input_without_echo(fields):
    async def run():
        async with environment_api() as (client, repository):
            await login(client, "alice")
            payload = {"user_id": "alice", "name": "DEMO", "value": "fixture-sensitive", **fields}
            # JSON 转义使非法 Unicode 也抵达服务端实际校验链路。
            import json

            response = await client.post(
                "/api/environment",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422
            assert "fixture-sensitive" not in response.text
            assert await repository.list_environment("alice") == []

    asyncio.run(run())


def test_http_cookie_identity_origin_and_revocation_guard_every_write():
    async def run():
        async with environment_api() as (client, repository):
            payload = {"user_id": "alice", "name": "DEMO", "value": "fixture-sensitive"}
            response = await client.post("/api/environment", json=payload)
            assert response.status_code == 401 and "fixture-sensitive" not in response.text
            await login(client, "alice")
            response = await client.post("/api/environment", json={**payload, "user_id": "admin"})
            assert response.status_code == 409
            response = await client.post(
                "/api/environment", json=payload, headers={"Origin": "https://untrusted.test"}
            )
            assert response.status_code == 403
            response = await client.post(
                "/api/environment", json=payload, headers={"X-Melonclaw-User": "admin"}
            )
            assert response.status_code == 409
            async with repository.engine.begin() as conn:
                await conn.execute(
                    update(tenants).where(tenants.c.tenant_id == "team-a").values(enabled=False)
                )
            response = await client.post("/api/environment", json=payload)
            assert response.status_code == 401
            assert await repository.list_environment("alice") == []

    asyncio.run(run())


def test_http_concurrent_edits_use_version_cas_and_preserve_winner():
    async def run():
        async with environment_api() as (client, repository):
            await login(client, "alice")
            created = await client.post(
                "/api/environment",
                json={"user_id": "alice", "name": "DEMO", "value": "original-fixture"},
            )
            assert created.status_code == 201
            path = f"/api/environment/{created.json()['id']}"
            responses = await asyncio.gather(
                *(
                    client.patch(
                        path, json={"user_id": "alice", "expected_version": 1, "value": value}
                    )
                    for value in ("first-fixture", "second-fixture")
                )
            )
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = ("first-fixture", "second-fixture")[
                next(
                    index for index, response in enumerate(responses) if response.status_code == 200
                )
            ]
            assert await repository.effective_environment("alice") == {"DEMO": winner}
            assert all(winner not in response.text for response in responses)
            items = (await client.get("/api/environment?user_id=alice")).json()["items"]
            assert items[0]["version"] == 2

    asyncio.run(run())
