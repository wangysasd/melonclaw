"""模型供应商与模型配置：校验、脱敏展示、解析与权限矩阵。"""

import unittest
from types import SimpleNamespace

from melonclaw.core.model_catalog import (
    catalog_item,
    custom_model_id,
    resolve_model_row,
)
from melonclaw.services.resource_service import (
    ModelConfigError,
    ModelConfigPayload,
    ProviderConfigPayload,
    ResourceNotFoundError,
    ResourcePermissionError,
    ResourceService,
    model_public_dict,
    provider_public_dict,
    validate_model_payload,
    validate_provider_payload,
)


def make_provider_row(**overrides):
    row = {
        "provider_key": "my-gateway",
        "scope": "global",
        "source_type": "manual",
        "display_name": "我的网关",
        "provider_type": "openai_compatible",
        "api_key_env": "",
        "request_headers": {},
        "extra_config": {},
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret",
        "models_endpoint": "https://api.example.com/v1/models",
        "enabled": True,
        "created_by": "admin-1",
        "version": 2,
    }
    row.update(overrides)
    return row


def make_row(**overrides):
    row = {
        "model_key": "my-gpt",
        "provider_key": "my-gateway",
        "scope": "global",
        "source_type": "manual",
        "display_name": "我的 GPT",
        "model_name": "gpt-4o-mini",
        "enabled": True,
        "is_default": False,
        "input_modalities": ["text"],
        "context_window": 1_000_000,
        "created_by": "admin-1",
        "version": 3,
    }
    row.update(overrides)
    return row


def make_provider_payload(**overrides):
    values = {
        "provider_key": "my-gateway",
        "scope": "global",
        "display_name": "我的网关",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret",
        "models_endpoint": "https://api.example.com/v1/models",
    }
    values.update(overrides)
    return ProviderConfigPayload(**values)


def make_payload(**overrides):
    values = {
        "model_key": "my-gpt",
        "provider_key": "my-gateway",
        "scope": "global",
        "display_name": "我的 GPT",
        "model_name": "gpt-4o-mini",
    }
    values.update(overrides)
    return ModelConfigPayload(**values)


class ValidateProviderPayloadTests(unittest.TestCase):
    def test_rejects_uppercase_provider_key(self):
        with self.assertRaises(ModelConfigError):
            validate_provider_payload(make_provider_payload(provider_key="MyGW"))

    def test_rejects_bad_base_url(self):
        with self.assertRaises(ModelConfigError):
            validate_provider_payload(
                make_provider_payload(base_url="ftp://example.com")
            )

    def test_rejects_bad_models_endpoint(self):
        with self.assertRaises(ModelConfigError):
            validate_provider_payload(
                make_provider_payload(models_endpoint="not-a-url")
            )

    def test_allows_empty_models_endpoint_and_missing_key(self):
        validate_provider_payload(
            make_provider_payload(models_endpoint="", api_key=None)
        )


class ValidateModelPayloadTests(unittest.TestCase):
    def test_rejects_uppercase_model_key(self):
        with self.assertRaises(ModelConfigError):
            validate_model_payload(make_payload(model_key="MyGPT"))

    def test_rejects_missing_provider_key(self):
        with self.assertRaises(ModelConfigError):
            validate_model_payload(make_payload(provider_key="  "))

    def test_rejects_empty_model_name(self):
        with self.assertRaises(ModelConfigError):
            validate_model_payload(make_payload(model_name="  "))


class ProviderPublicDictTests(unittest.TestCase):
    def test_api_key_never_leaks(self):
        public = provider_public_dict(make_provider_row())
        self.assertTrue(public["has_api_key"])
        self.assertTrue(public["effective_has_key"])
        self.assertFalse(public["has_my_key"])
        self.assertEqual(public["enabled_models_count"], 0)
        self.assertFalse(public["enabled"] is False)
        self.assertNotIn("api_key", public)
        self.assertNotIn("sk-secret", str(public))

    def test_missing_key_flagged(self):
        public = provider_public_dict(make_provider_row(api_key=None))
        self.assertFalse(public["has_api_key"])
        self.assertFalse(public["effective_has_key"])

    def test_my_key_counts_as_effective(self):
        public = provider_public_dict(
            make_provider_row(api_key=None),
            has_my_key=True,
            enabled_models_count=2,
        )
        self.assertTrue(public["has_my_key"])
        self.assertTrue(public["effective_has_key"])
        self.assertEqual(public["enabled_models_count"], 2)


class PublicDictTests(unittest.TestCase):
    def test_joins_provider_display_name_without_secrets(self):
        public = model_public_dict(make_row(), make_provider_row())
        self.assertEqual(public["provider_key"], "my-gateway")
        self.assertEqual(public["provider_display_name"], "我的网关")
        self.assertFalse(public["is_default"])
        self.assertEqual(public["input_modalities"], ["text"])
        self.assertNotIn("api_key", public)
        self.assertNotIn("base_url", public)
        self.assertNotIn("sk-secret", str(public))

    def test_default_and_modalities_exposed(self):
        public = model_public_dict(
            make_row(is_default=True, input_modalities=["text", "image"]),
            make_provider_row(),
        )
        self.assertTrue(public["is_default"])
        self.assertEqual(public["input_modalities"], ["text", "image"])


class ResolveModelRowTests(unittest.TestCase):
    def test_resolves_with_versions_in_cache_key(self):
        resolved = resolve_model_row(make_row(), make_provider_row())
        self.assertEqual(resolved.profile_id, custom_model_id("my-gpt"))
        self.assertEqual(resolved.source, "system")
        self.assertEqual(resolved.provider, "my-gateway")
        self.assertEqual(resolved.adapter_type, "openai_compatible")
        self.assertEqual(resolved.base_url, "https://api.example.com/v1")
        self.assertEqual(resolved.config_version, 3)
        self.assertEqual(resolved.provider_version, 2)
        self.assertEqual(
            resolved.cache_key,
            (
                "custom:my-gpt",
                3,
                2,
                "my-gateway",
                "gpt-4o-mini",
                "https://api.example.com/v1",
            ),
        )
        self.assertNotIn("sk-secret", str(resolved.public_dict()))

    def test_system_row_resolves_as_system_source(self):
        resolved = resolve_model_row(
            make_row(scope="global", source_type="system", model_key="deepseek-flash"),
            make_provider_row(provider_key="deepseek", source_type="system"),
        )
        self.assertEqual(resolved.source, "system")
        self.assertEqual(resolved.profile_id, "custom:deepseek-flash")

    def test_modalities_come_from_row(self):
        resolved = resolve_model_row(
            make_row(input_modalities=["text", "image"]),
            make_provider_row(),
        )
        self.assertEqual(resolved.input_modalities, frozenset({"text", "image"}))

    def test_disabled_model_row_rejected(self):
        with self.assertRaises(ValueError):
            resolve_model_row(make_row(enabled=False), make_provider_row())

    def test_disabled_provider_row_rejected(self):
        with self.assertRaises(ValueError):
            resolve_model_row(make_row(), make_provider_row(enabled=False))

    def test_missing_api_key_rejected(self):
        with self.assertRaises(ValueError):
            resolve_model_row(make_row(), make_provider_row(api_key=None))

    def test_catalog_item_unavailable_without_key_or_disabled(self):
        item = catalog_item(make_row(), make_provider_row(api_key=None))
        self.assertFalse(item["available"])
        item = catalog_item(make_row(), make_provider_row(enabled=False))
        self.assertFalse(item["available"])
        item = catalog_item(make_row(enabled=False), make_provider_row())
        self.assertFalse(item["available"])

    def test_personal_model_source_is_custom(self):
        row = make_row(scope="user")
        self.assertEqual(catalog_item(row, make_provider_row())["source"], "custom")
        self.assertEqual(resolve_model_row(row, make_provider_row()).source, "custom")

    def test_catalog_item_available_with_enabled_pair(self):
        item = catalog_item(make_row(), make_provider_row())
        self.assertTrue(item["available"])
        self.assertEqual(item["scope"], "global")
        self.assertEqual(item["source"], "system")
        self.assertEqual(item["provider"], "我的网关")
        self.assertEqual(item["provider_key"], "my-gateway")

    def test_catalog_item_maps_system_source_and_default(self):
        item = catalog_item(
            make_row(scope="global", source_type="system", is_default=True),
            make_provider_row(source_type="system"),
        )
        self.assertEqual(item["source"], "system")
        self.assertTrue(item["is_default"])


class FakeStorage:
    def __init__(self):
        self.providers = {}
        self.rows = {}
        self.default_key = None
        self.user_keys: dict[tuple[str, str], str] = {}

    async def get_user_context(self, user_id):
        roles = {"admin-1": "admin", "owner-1": "owner"}
        return SimpleNamespace(tenant_role=roles.get(user_id, "member"))

    # ---- providers ----

    async def get_provider_row(self, provider_key):
        return self.providers.get(provider_key)

    async def create_provider_row(self, **fields):
        self.providers[fields["provider_key"]] = dict(fields, version=1)
        return self.providers[fields["provider_key"]]

    async def update_provider_row(self, provider_key, **fields):
        self.providers[provider_key].update(fields)

    async def delete_provider_row(self, provider_key):
        self.providers.pop(provider_key, None)

    async def list_models_of_provider(self, provider_key):
        return [
            row
            for row in self.rows.values()
            if row["provider_key"] == provider_key
        ]

    async def list_provider_rows(self, *, include_disabled=False):
        return [
            row
            for row in self.providers.values()
            if include_disabled or row["enabled"]
        ]

    async def get_user_provider_key(self, provider_key, user_id):
        key = self.user_keys.get((provider_key, user_id))
        if key is None:
            return None
        return {"provider_key": provider_key, "user_id": user_id, "api_key": key}

    async def list_user_provider_keys(self, user_id):
        return {
            provider_key: True
            for (provider_key, owner), _ in self.user_keys.items()
            if owner == user_id
        }

    async def upsert_user_provider_key(self, *, provider_key, user_id, api_key):
        self.user_keys[(provider_key, user_id)] = api_key

    async def delete_user_provider_key(self, provider_key, user_id):
        self.user_keys.pop((provider_key, user_id), None)

    async def effective_provider_api_key(self, provider_row, user_id):
        override = self.user_keys.get(
            (str(provider_row["provider_key"]), user_id)
        )
        if override:
            return override
        return provider_row.get("api_key")

    # ---- models ----

    async def get_model_row(self, model_key):
        return self.rows.get(model_key)

    async def create_model_row(self, **fields):
        fields.setdefault("is_default", False)
        fields.setdefault("input_modalities", ["text"])
        self.rows[fields["model_key"]] = dict(fields, version=1)
        return self.rows[fields["model_key"]]

    async def update_model_row(self, model_key, **fields):
        self.rows[model_key].update(fields)

    async def set_default_model(self, model_key):
        self.default_key = model_key
        target = self.rows[model_key]
        for row in self.rows.values():
            if row["scope"] == target["scope"] and (row["scope"] == "global" or row["created_by"] == target["created_by"]):
                row["is_default"] = row["model_key"] == model_key

    async def delete_model_row(self, model_key):
        self.rows.pop(model_key, None)

    async def list_visible_model_rows(self, user_id, *, include_disabled=False):
        return [
            row
            for row in self.rows.values()
            if row["scope"] == "global" or row["created_by"] == user_id
        ]


def make_service(storage):
    runtime = SimpleNamespace(
        require_ready=lambda: storage,
        settings=SimpleNamespace(data_root=None),
    )
    return ResourceService(runtime)


class ProviderPermissionMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_cannot_create_user_scope_provider(self):
        storage = FakeStorage()
        service = make_service(storage)
        with self.assertRaises(ResourcePermissionError):
            await service.create_provider("member-1", make_provider_payload(scope="user"))
        self.assertFalse(storage.providers)

    async def test_member_cannot_create_global_provider(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ResourcePermissionError):
            await service.create_provider(
                "member-1", make_provider_payload(scope="global")
            )

    async def test_admin_creates_global_provider(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        self.assertEqual(storage.providers["my-gateway"]["scope"], "global")

    async def test_member_cannot_modify_global_provider(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        with self.assertRaises(ResourcePermissionError):
            await service.update_provider("member-1", "my-gateway", enabled=False)
        with self.assertRaises(ResourcePermissionError):
            await service.delete_provider("member-1", "my-gateway")

    async def test_admin_cannot_create_private_provider(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ModelConfigError):
            await service.create_provider("admin-1", make_provider_payload(scope="user"))

    async def test_member_cannot_modify_provider_even_if_creator(self):
        storage = FakeStorage()
        storage.providers["my-gateway"] = make_provider_row(created_by="member-1")
        service = make_service(storage)
        with self.assertRaises(ResourcePermissionError):
            await service.update_provider("member-1", "my-gateway", enabled=False)
        with self.assertRaises(ResourcePermissionError):
            await service.delete_provider("member-1", "my-gateway")

    async def test_duplicate_provider_key_rejected(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        with self.assertRaises(ModelConfigError):
            await service.create_provider("admin-1", make_provider_payload())

    async def test_update_without_api_key_keeps_existing(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.update_provider("admin-1", "my-gateway", display_name="改名")
        self.assertEqual(storage.providers["my-gateway"]["api_key"], "sk-secret")

    async def test_system_provider_cannot_be_deleted(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        storage.providers["my-gateway"]["source_type"] = "system"
        with self.assertRaises(ModelConfigError):
            await service.delete_provider("admin-1", "my-gateway")

    async def test_provider_with_models_cannot_be_deleted(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        with self.assertRaises(ModelConfigError):
            await service.delete_provider("admin-1", "my-gateway")
        await service.delete_model("admin-1", "my-gpt")
        await service.delete_provider("admin-1", "my-gateway")
        self.assertNotIn("my-gateway", storage.providers)

    async def test_disabling_provider_revokes_shared_credentials_only(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.set_user_provider_key("member-1", "my-gateway", "personal-test-key")
        await service.update_provider(
            "admin-1", "my-gateway", enabled=False,
            api_key="replacement-test-key", api_key_env="MINIMAX_API_KEY",
        )
        row = storage.providers["my-gateway"]
        self.assertFalse(row["enabled"])
        self.assertIsNone(row["api_key"])
        self.assertEqual(row["api_key_env"], "")
        self.assertFalse(provider_public_dict(row)["has_api_key"])
        self.assertEqual(storage.user_keys[("my-gateway", "member-1")], "personal-test-key")
        await service.update_provider("admin-1", "my-gateway", enabled=True)
        self.assertIsNone(row["api_key"])
        self.assertEqual(row["api_key_env"], "")

    async def test_shared_provider_does_not_imply_personal_key(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        await service.set_user_provider_key("member-1", "my-gateway", "test-personal-key")
        for user_id, has_key in [("member-1", True), ("member-2", False)]:
            items = (await service.list_providers(user_id))["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["has_my_key"], has_key)
            self.assertTrue(items[0]["enabled"])
            self.assertEqual(len((await service.list_models(user_id))["items"]), 1)

    async def test_user_key_override(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.set_user_provider_key("member-1", "my-gateway", "sk-user")
        self.assertEqual(
            storage.user_keys[("my-gateway", "member-1")], "sk-user"
        )
        providers = await service.list_providers("member-1")
        item = next(
            p for p in providers["items"] if p["provider_key"] == "my-gateway"
        )
        self.assertTrue(item["has_my_key"])
        self.assertTrue(item["effective_has_key"])
        await service.delete_user_provider_key("member-1", "my-gateway")
        self.assertNotIn(("my-gateway", "member-1"), storage.user_keys)

    async def test_list_providers_includes_model_counts(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        providers = await service.list_providers("member-1")
        item = next(
            p for p in providers["items"] if p["provider_key"] == "my-gateway"
        )
        self.assertEqual(item["enabled_models_count"], 1)


class ModelPermissionMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_creates_user_scope(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider(
            "admin-1", make_provider_payload(scope="global")
        )
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model(
            "member-1", make_payload(scope="user")
        )
        self.assertIn("my-gpt", storage.rows)

    async def test_member_creates_user_model_under_global_provider(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model(
            "member-1", make_payload(scope="user")
        )
        self.assertEqual(storage.rows["my-gpt"]["scope"], "user")

    async def test_member_cannot_create_global(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ResourcePermissionError):
            await service.create_model("member-1", make_payload(scope="global"))

    async def test_admin_creates_global(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        await service.create_model("admin-1", make_payload(scope="global"))
        self.assertEqual(storage.rows["my-gpt"]["scope"], "global")

    async def test_missing_provider_rejected(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ModelConfigError):
            await service.create_model("admin-1", make_payload())

    async def test_shared_provider_available_to_other_users(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        storage.user_keys[("my-gateway", "member-2")] = "test-personal-key"
        await service.create_model("member-2", make_payload(scope="user"))
        self.assertEqual(storage.rows["my-gpt"]["created_by"], "member-2")

    async def test_disabled_provider_rejected(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.update_provider("admin-1", "my-gateway", enabled=False)
        with self.assertRaises(ModelConfigError):
            await service.create_model("admin-1", make_payload())

    async def test_member_cannot_modify_global(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        await service.create_model("admin-1", make_payload(scope="global"))
        with self.assertRaises(ResourcePermissionError):
            await service.update_model("member-1", "my-gpt", enabled=False)
        with self.assertRaises(ResourcePermissionError):
            await service.delete_model("member-1", "my-gpt")

    async def test_admin_cannot_modify_others_private(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider(
            "admin-1", make_provider_payload(scope="global")
        )
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model(
            "member-1", make_payload(scope="user")
        )
        with self.assertRaises(ResourcePermissionError):
            await service.update_model("admin-1", "my-gpt", enabled=False)
        with self.assertRaises(ResourcePermissionError):
            await service.delete_model("admin-1", "my-gpt")

    async def test_owner_modifies_own_private(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider(
            "admin-1", make_provider_payload(scope="global")
        )
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model(
            "member-1", make_payload(scope="user")
        )
        await service.update_model("member-1", "my-gpt", enabled=False)
        self.assertFalse(storage.rows["my-gpt"]["enabled"])
        await service.delete_model("member-1", "my-gpt")
        self.assertNotIn("my-gpt", storage.rows)

    async def test_missing_row_is_404(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ResourceNotFoundError):
            await service.update_model("admin-1", "nope", enabled=False)

    async def test_duplicate_key_rejected(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        with self.assertRaises(ModelConfigError):
            await service.create_model("admin-1", make_payload())


class SystemModelProtectionTests(unittest.IsolatedAsyncioTestCase):
    """平台种子模型（source_type='system'）：管理员可编辑和删除，普通用户无权删除。"""

    async def _seed_system_model(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        await service.create_model("admin-1", make_payload(scope="global"))
        storage.rows["my-gpt"]["source_type"] = "system"
        return storage, service

    async def test_admin_can_delete_system_model(self):
        storage, service = await self._seed_system_model()
        await service.delete_model("admin-1", "my-gpt")
        self.assertNotIn("my-gpt", storage.rows)

    async def test_member_cannot_delete_system_model(self):
        storage, service = await self._seed_system_model()
        with self.assertRaises(ResourcePermissionError):
            await service.delete_model("member-1", "my-gpt")
        self.assertIn("my-gpt", storage.rows)

    async def test_system_model_can_be_updated(self):
        storage, service = await self._seed_system_model()
        await service.update_model("admin-1", "my-gpt", enabled=False)
        self.assertFalse(storage.rows["my-gpt"]["enabled"])


class DefaultModelSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_global_model(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload(scope="global"))
        await service.create_model("admin-1", make_payload(scope="global"))
        return storage, service

    async def test_admin_sets_default_on_global_model(self):
        storage, service = await self._seed_global_model()
        await service.update_model("admin-1", "my-gpt", is_default=True)
        self.assertEqual(storage.default_key, "my-gpt")
        self.assertTrue(storage.rows["my-gpt"]["is_default"])

    async def test_member_cannot_set_default(self):
        storage, service = await self._seed_global_model()
        with self.assertRaises(ResourcePermissionError):
            await service.update_model("member-1", "my-gpt", is_default=True)

    async def test_private_model_can_be_default(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider(
            "admin-1", make_provider_payload(scope="global")
        )
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model(
            "member-1", make_payload(scope="user")
        )
        await service.update_model("member-1", "my-gpt", is_default=True)
        self.assertTrue(storage.rows["my-gpt"]["is_default"])

    async def test_unset_default_rejected(self):
        storage, service = await self._seed_global_model()
        with self.assertRaises(ModelConfigError):
            await service.update_model("admin-1", "my-gpt", is_default=False)


class FetchRemoteModelsTests(unittest.IsolatedAsyncioTestCase):
    """远端 /models 拉取：不落库，错误转成业务提示。"""

    def _service_with_provider(self):
        storage = FakeStorage()
        service = make_service(storage)
        return storage, service

    async def test_missing_endpoint_rejected(self):
        _storage, service = self._service_with_provider()
        await service.create_provider(
            "admin-1", make_provider_payload(models_endpoint=None)
        )
        with self.assertRaises(ModelConfigError):
            await service.fetch_remote_models("member-1", "my-gateway")

    async def test_fetch_normalizes_remote_list(self):
        import melonclaw.services.resource_service as resource_module

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"data": [{"id": "gpt-4o", "name": "GPT-4o"}, {"id": ""}]}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                self.url = url
                assert headers["Authorization"] == "Bearer test-personal-key"
                return FakeResponse()

        _storage, service = self._service_with_provider()
        await service.create_provider("admin-1", make_provider_payload())
        _storage.providers["my-gateway"]["enabled"] = False
        _storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        original = resource_module.httpx.AsyncClient
        resource_module.httpx.AsyncClient = FakeClient
        try:
            result = await service.fetch_remote_models("member-1", "my-gateway")
        finally:
            resource_module.httpx.AsyncClient = original
        self.assertEqual(
            result["items"], [{"id": "gpt-4o", "display_name": "GPT-4o"}]
        )

    async def test_unknown_provider_rejected(self):
        service = make_service(FakeStorage())
        with self.assertRaises(ResourceNotFoundError):
            await service.fetch_remote_models("member-2", "missing")

    async def test_unauthorized_becomes_key_hint(self):
        import melonclaw.services.resource_service as resource_module

        class FakeResponse:
            status_code = 401

            def json(self):
                return {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                return FakeResponse()

        _storage, service = self._service_with_provider()
        await service.create_provider("admin-1", make_provider_payload())
        original = resource_module.httpx.AsyncClient
        resource_module.httpx.AsyncClient = FakeClient
        try:
            with self.assertRaises(ModelConfigError) as ctx:
                await service.fetch_remote_models("member-1", "my-gateway")
        finally:
            resource_module.httpx.AsyncClient = original
        self.assertIn("API Key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class PersonalProviderCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_key_allows_creation_when_global_disabled(self):
        storage = FakeStorage()
        storage.providers["my-gateway"] = make_provider_row(enabled=False)
        service = make_service(storage)
        with self.assertRaisesRegex(ModelConfigError, "个人 API Key"):
            await service.create_model("member-1", make_payload(scope="user"))
        storage.user_keys[("my-gateway", "member-1")] = "test-personal-key"
        await service.create_model("member-1", make_payload(scope="user"))
        self.assertEqual(storage.rows["my-gpt"]["scope"], "user")
        self.assertFalse(storage.providers["my-gateway"]["enabled"])
        with self.assertRaisesRegex(ModelConfigError, "个人 API Key"):
            await service.create_model("member-2", make_payload(scope="user", model_key="other"))


class ModelContextWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_window_and_update_roundtrip(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        row = storage.rows["my-gpt"]
        provider = storage.providers["my-gateway"]
        self.assertEqual(row["context_window"], 1_000_000)
        await service.update_model("admin-1", "my-gpt", context_window=128_000)
        self.assertEqual(model_public_dict(row, provider)["context_window"], 128_000)
        self.assertEqual(catalog_item(row, provider)["context_window"], 128_000)
        resolved = resolve_model_row(row, provider)
        self.assertEqual(resolved.context_window, 128_000)
        self.assertEqual(resolved.public_dict()["context_window"], 128_000)
        from melonclaw.core.chat_model import build_chat_model
        model = build_chat_model(resolved)
        self.assertEqual(model.profile["max_input_tokens"], 128_000 - 4096)
        self.assertEqual(model.max_retries, 0)

    async def test_invalid_window_rejected_at_create_and_update(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        for value in (0, -1, 1.5, True, "1000000", 2_147_483_648):
            with self.subTest(value=value):
                with self.assertRaises(ModelConfigError):
                    validate_model_payload(make_payload(context_window=value))
                with self.assertRaises(ModelConfigError):
                    await service.update_model("admin-1", "my-gpt", context_window=value)
        self.assertEqual(storage.rows["my-gpt"]["context_window"], 1_000_000)

    def test_api_window_default_and_strict_validation(self):
        from pydantic import ValidationError

        from melonclaw.api.schemas import ModelConfigCreateRequest, ModelConfigUpdateRequest
        fields = dict(user_id="admin-1", model_key="model", provider_key="provider", scope="global",
                      display_name="Model", model_name="remote")
        self.assertEqual(ModelConfigCreateRequest(**fields).context_window, 1_000_000)
        self.assertIsNone(ModelConfigUpdateRequest(user_id="admin-1").context_window)
        for value in (0, -1, 1.5, True, "1000000", 2_147_483_648):
            with self.assertRaises(ValidationError):
                ModelConfigCreateRequest(**fields, context_window=value)

    async def test_member_cannot_change_shared_model_window(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_provider("admin-1", make_provider_payload())
        await service.create_model("admin-1", make_payload())
        with self.assertRaises(ResourcePermissionError):
            await service.update_model("member-1", "my-gpt", context_window=128_000)
