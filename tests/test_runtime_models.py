"""运行时模型目录与解析：数据库为唯一事实来源，无可用模型时提示配置。"""

import unittest
from types import SimpleNamespace

from melonclaw.services.runtime import ChatRuntime


def make_provider_row(provider_key="deepseek", **overrides):
    row = {
        "provider_key": provider_key,
        "scope": "global",
        "source_type": "system",
        "display_name": "DeepSeek",
        "provider_type": "openai_compatible",
        "api_key_env": "",
        "request_headers": {},
        "extra_config": {},
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-x",
        "models_endpoint": "https://api.deepseek.com/models",
        "enabled": True,
        "created_by": "admin",
        "version": 1,
    }
    row.update(overrides)
    return row


class FakeModelStorage:
    def __init__(self, rows=(), providers=()):
        self.rows = {row["model_key"]: row for row in rows}
        if isinstance(providers, dict):
            self.providers = providers or {"deepseek": make_provider_row()}
        else:
            self.providers = (
                {row["provider_key"]: row for row in providers}
                or {"deepseek": make_provider_row()}
            )
        self.user_keys: dict[tuple[str, str], str] = {}

    async def get_model_row(self, model_key):
        return self.rows.get(model_key)

    async def get_provider_row(self, provider_key):
        return self.providers.get(provider_key)

    async def get_user_provider_key(self, provider_key, user_id):
        key = self.user_keys.get((provider_key, user_id))
        return {"api_key": key} if key else None

    async def effective_provider_api_key(self, provider_row, user_id):
        override = self.user_keys.get(
            (str(provider_row["provider_key"]), user_id)
        )
        if override:
            return override
        return provider_row.get("api_key")

    async def get_model_with_provider(self, model_key):
        row = self.rows.get(model_key)
        if row is None:
            return None
        provider = self.providers.get(row["provider_key"])
        if provider is None:
            return None
        return row, provider

    async def list_visible_model_rows(self, user_id, *, include_disabled=False):
        rows = [
            row
            for row in self.rows.values()
            if row["scope"] == "global" or row["created_by"] == user_id
        ]
        if not include_disabled:
            rows = [row for row in rows if row["enabled"]]
        return rows


def make_row(model_key, **overrides):
    row = {
        "model_key": model_key,
        "provider_key": "deepseek",
        "scope": "global",
        "source_type": "system",
        "display_name": model_key,
        "model_name": model_key,
        "enabled": True,
        "is_default": False,
        "input_modalities": ["text"],
        "created_by": "admin",
        "version": 1,
    }
    row.update(overrides)
    return row


def make_runtime(storage):
    settings = SimpleNamespace()
    return ChatRuntime(
        settings=settings,
        storage=storage,
        checkpointer=object(),
        memory_store=object(),
        memory_service=object(),
    )


class ModelsCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_comes_from_database_rows(self):
        runtime = make_runtime(
            FakeModelStorage(
                [
                    make_row("deepseek-flash", is_default=True),
                    make_row("minimax-m3", display_name="MiniMax M3"),
                ]
            )
        )
        catalog = await runtime.models("anyone")
        self.assertEqual(
            [item["id"] for item in catalog["items"]],
            ["custom:deepseek-flash", "custom:minimax-m3"],
        )
        self.assertTrue(catalog["items"][0]["is_default"])
        self.assertEqual(catalog["items"][0]["provider"], "DeepSeek")
        self.assertEqual(catalog["items"][0]["provider_key"], "deepseek")
        self.assertEqual(catalog["default_model_id"], "custom:deepseek-flash")
        self.assertNotIn("sk-x", str(catalog))

    async def test_rows_without_provider_key_are_unavailable(self):
        runtime = make_runtime(
            FakeModelStorage(
                [make_row("minimax-m3", provider_key="other")],
                providers={
                    "other": make_provider_row(provider_key="other", api_key=None)
                },
            )
        )
        catalog = await runtime.models("anyone")
        self.assertFalse(catalog["items"][0]["available"])
        self.assertEqual(len(catalog["items"]), 1)
        self.assertEqual(catalog["default_model_id"], "")

    async def test_disabled_provider_makes_rows_unavailable(self):
        runtime = make_runtime(
            FakeModelStorage(
                [make_row("minimax-m3")],
                providers={"deepseek": make_provider_row(enabled=False)},
            )
        )
        catalog = await runtime.models("anyone")
        self.assertFalse(catalog["items"][0]["available"])
        self.assertEqual(catalog["default_model_id"], "")

    async def test_empty_database_returns_empty_catalog(self):
        runtime = make_runtime(FakeModelStorage())
        catalog = await runtime.models("anyone")
        self.assertEqual(catalog, {"items": [], "default_model_id": ""})


class ResolveModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_default_precedes_global_and_is_isolated(self):
        storage = FakeModelStorage([
            make_row("global", is_default=True),
            make_row("personal", scope="user", created_by="member-1", is_default=True),
        ])
        storage.user_keys[("deepseek", "member-1")] = "test-personal"
        runtime = make_runtime(storage)
        self.assertEqual((await runtime.models("member-1"))["default_model_id"], "custom:personal")
        self.assertEqual((await runtime.resolve_model("member-1")).profile_id, "custom:personal")
        self.assertEqual((await runtime.resolve_model("member-2")).profile_id, "custom:global")
        storage.user_keys.clear()
        self.assertEqual((await runtime.resolve_model("member-1")).profile_id, "custom:global")


    async def test_personal_key_is_isolated_and_shared_models_remain_available(self):
        storage = FakeModelStorage([make_row("deepseek-flash", is_default=True)])
        runtime = make_runtime(storage)
        shared = await runtime.resolve_model("member-1", "custom:deepseek-flash")
        storage.user_keys[("deepseek", "member-1")] = "test-personal-key"
        personal = await runtime.resolve_model("member-1", "custom:deepseek-flash")
        other = await runtime.resolve_model("member-2", "custom:deepseek-flash")
        self.assertEqual(personal.api_key, "test-personal-key")
        self.assertEqual(other.api_key, shared.api_key)
        del storage.user_keys[("deepseek", "member-1")]
        restored = await runtime.resolve_model("member-1", "custom:deepseek-flash")
        self.assertEqual(restored.api_key, shared.api_key)

    async def test_none_selects_default_row(self):
        runtime = make_runtime(
            FakeModelStorage(
                [
                    make_row("deepseek-pro"),
                    make_row("deepseek-flash", is_default=True),
                ]
            )
        )
        resolved = await runtime.resolve_model("anyone")
        self.assertEqual(resolved.profile_id, "custom:deepseek-flash")
        self.assertEqual(resolved.source, "system")

    async def test_none_without_default_flag_selects_first_available(self):
        runtime = make_runtime(
            FakeModelStorage(
                [
                    make_row("minimax-m3", provider_key="no-key"),
                    make_row("deepseek-pro"),
                ]
            )
        )
        # no-key 供应商未配 Key，被跳过，选中 deepseek-pro。
        resolved = await runtime.resolve_model("anyone")
        self.assertEqual(resolved.profile_id, "custom:deepseek-pro")

    async def test_none_with_empty_database_requires_configuration(self):
        runtime = make_runtime(FakeModelStorage())
        with self.assertRaisesRegex(ValueError, "暂无可用模型"):
            await runtime.resolve_model("anyone")

    async def test_explicit_row_resolution(self):
        runtime = make_runtime(
            FakeModelStorage(
                [
                    make_row("deepseek-flash", is_default=True),
                    make_row("minimax-m3", display_name="MiniMax M3"),
                ]
            )
        )
        resolved = await runtime.resolve_model("anyone", "custom:minimax-m3")
        self.assertEqual(resolved.display_name, "MiniMax M3")
        self.assertEqual(resolved.config_version, 1)
        self.assertEqual(resolved.provider_version, 1)

    async def test_other_users_private_model_rejected(self):
        runtime = make_runtime(
            FakeModelStorage(
                [
                    make_row("my-gpt", scope="user", created_by="user-1"),
                    make_row("deepseek-flash", is_default=True),
                ]
            )
        )
        with self.assertRaises(ValueError):
            await runtime.resolve_model("user-2", "custom:my-gpt")

    async def test_disabled_row_rejected(self):
        runtime = make_runtime(
            FakeModelStorage(
                [make_row("deepseek-flash", is_default=True, enabled=False)]
            )
        )
        with self.assertRaises(ValueError):
            await runtime.resolve_model("anyone", "custom:deepseek-flash")

    async def test_disabled_provider_rejected(self):
        runtime = make_runtime(
            FakeModelStorage(
                [make_row("deepseek-flash", is_default=True)],
                providers={"deepseek": make_provider_row(enabled=False)},
            )
        )
        with self.assertRaises(ValueError):
            await runtime.resolve_model("anyone", "custom:deepseek-flash")

    async def test_unknown_id_rejected(self):
        runtime = make_runtime(FakeModelStorage())
        with self.assertRaises(ValueError):
            await runtime.resolve_model("anyone", "system:deepseek:flash")

    async def test_missing_row_rejected(self):
        runtime = make_runtime(
            FakeModelStorage([make_row("deepseek-pro", is_default=True)])
        )
        with self.assertRaises(ValueError):
            await runtime.resolve_model("anyone", "custom:deepseek-flash")

    async def test_model_for_message_recovers_from_deleted_custom_model(self):
        runtime = make_runtime(
            FakeModelStorage([make_row("deepseek-flash", is_default=True)])
        )
        message = {"model": {"id": "custom:deleted-model", "model": "gone"}}
        resolved = await runtime.model_for_message("anyone", message)
        self.assertEqual(resolved.profile_id, "custom:deepseek-flash")


if __name__ == "__main__":
    unittest.main()


class PersonalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_model_independent_of_global_switch(self):
        storage = FakeModelStorage(
            [make_row("private", scope="user", created_by="member-1"),
             make_row("shared")],
            [make_provider_row(enabled=False)],
        )
        storage.user_keys[("deepseek", "member-1")] = "test-personal-key"
        runtime = make_runtime(storage)
        catalog = await runtime.models("member-1")
        items = {item["id"]: item for item in catalog["items"]}
        self.assertTrue(items["custom:private"]["available"])
        self.assertFalse(items["custom:shared"]["available"])
        explicit = await runtime.resolve_model("member-1", "custom:private")
        default = await runtime.resolve_model("member-1")
        self.assertEqual(explicit.api_key, "test-personal-key")
        self.assertEqual(default.profile_id, explicit.profile_id)
        with self.assertRaises(ValueError):
            await runtime.resolve_model("member-2", "custom:private")
        storage.user_keys.clear()
        storage.providers["deepseek"]["enabled"] = True
        catalog = await runtime.models("member-1")
        private = next(item for item in catalog["items"] if item["id"] == "custom:private")
        self.assertFalse(private["available"])
        with self.assertRaises(ValueError):
            await runtime.resolve_model("member-1", "custom:private")
