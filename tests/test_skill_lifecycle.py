"""可恢复 Skill 生命周期：跨实例、并发预览、版本追踪和真实文件操作。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_skill_import import FakeStorage, make_zip

from melonclaw.services.skill_content import content_manifest
from melonclaw.services.skill_import import SkillImportError, SkillImportService
from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skill_snapshot import skill_snapshot
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import SkillCatalog, SkillRoot


class ProcessStopped(BaseException):
    """模拟来不及跑 except Exception 补偿的进程中断。"""


def package(body="初版", extra=None):
    return make_zip(
        {
            "SKILL.md": f"---\nname: demo\ndescription: 演示\n---\n# 示例\n{body}\n".encode(),
            **(extra or {}),
        }
    )


class SkillLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = FakeStorage()
        self.service = SkillImportService(self.root)
        self.operations = SkillOperations(self.root)
        self.target = self.root / "skills/users/u1/demo"

    async def install(self):
        draft = await self.service.prepare(
            user_id="u1", archive_bytes=package(), storage=self.storage
        )
        await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        row = self.storage.created[0]
        row["enabled"] = True
        return row

    async def update_draft(self, row, body="新版", **kwargs):
        return await self.service.prepare(
            user_id="u1",
            archive_bytes=package(body),
            storage=self.storage,
            target_id=str(row["id"]),
            **kwargs,
        )

    async def recover(self):
        async with self.operations.locked():
            return await self.operations.recover(self.storage)

    async def test_drafts_survive_new_instances_and_other_prepares(self):
        draft = await self.service.prepare(
            user_id="u1", archive_bytes=package(), storage=self.storage
        )
        other = SkillImportService(self.root)
        await other.prepare(user_id="u2", archive_bytes=package(), storage=self.storage)
        await other.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        self.assertTrue((self.target / "SKILL.md").is_file())

    async def test_update_keeps_identity_preference_and_tracks_source(self):
        row = await self.install()
        identity = row["id"]
        draft = await self.update_draft(
            row, source_type="remote", source_url="https://github.com/o/r", source_ref="a" * 40
        )
        self.assertEqual(draft.preview["changes"]["modified"], ["SKILL.md"])
        self.assertIn("+新版", draft.preview["diff"])
        await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        self.assertEqual(len(self.storage.created), 1)
        self.assertEqual(row["id"], identity)
        self.assertTrue(row["enabled"])
        self.assertEqual(row["version"], 2)
        self.assertEqual(row["source_ref"], "a" * 40)
        self.assertEqual(row["content_hash"], content_manifest(self.target)[0])
        self.assertIn("新版", (self.target / "SKILL.md").read_text())

    async def test_stale_update_and_out_of_band_edits_require_new_preview(self):
        row = await self.install()
        first = await self.update_draft(row, "第一版")
        second = await self.update_draft(row, "第二版")
        await self.service.confirm(user_id="u1", draft_id=first.draft_id, storage=self.storage)
        with self.assertRaisesRegex(SkillImportError, "内容已变化"):
            await self.service.confirm(user_id="u1", draft_id=second.draft_id, storage=self.storage)
        third = await self.update_draft(row)
        (self.target / "extra.txt").write_text("手工改动")
        with self.assertRaisesRegex(SkillImportError, "内容已变化"):
            await self.service.confirm(user_id="u1", draft_id=third.draft_id, storage=self.storage)

    async def test_permissions_and_scope_rechecked_on_confirmation(self):
        row = await self.install()
        draft = await self.update_draft(row)
        row["created_by"] = "u2"
        with self.assertRaisesRegex(SkillImportError, "无权更新"):
            await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        with self.assertRaisesRegex(SkillImportError, "无权更新"):
            await self.update_draft(row)
        self.storage.get_user_context = AsyncMock(return_value=SimpleNamespace(tenant_role="admin"))
        with self.assertRaisesRegex(SkillImportError, "无权更新"):
            await self.update_draft(row)

    async def test_crash_after_file_swap_rolls_back_update(self):
        row = await self.install()
        draft = await self.update_draft(row)
        original = self.storage.update_skill_row

        async def crash(skill_id, **fields):
            if fields.get("status") == "ready":
                raise ProcessStopped()
            await original(skill_id, **fields)

        self.storage.update_skill_row = crash
        with self.assertRaises(ProcessStopped):
            await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        self.assertIn("新版", (self.target / "SKILL.md").read_text())
        self.assertEqual(row["status"], "updating")
        self.storage.update_skill_row = original
        await self.recover()
        self.assertIn("初版", (self.target / "SKILL.md").read_text())
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(await self.recover(), [])

    async def test_crash_after_database_commit_keeps_new_content(self):
        row = await self.install()
        draft = await self.update_draft(row)
        original = self.storage.update_skill_row

        async def crash(skill_id, **fields):
            await original(skill_id, **fields)
            if fields.get("status") == "ready":
                raise ProcessStopped()

        self.storage.update_skill_row = crash
        with self.assertRaises(ProcessStopped):
            await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        self.storage.update_skill_row = original
        await self.recover()
        self.assertIn("新版", (self.target / "SKILL.md").read_text())
        self.assertEqual(row["version"], 2)

    async def test_crash_before_install_commit_removes_pending_row_and_files(self):
        draft = await self.service.prepare(
            user_id="u1", archive_bytes=package(), storage=self.storage
        )
        original = self.storage.update_skill_row
        self.storage.update_skill_row = AsyncMock(side_effect=ProcessStopped())
        with self.assertRaises(ProcessStopped):
            await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        self.storage.update_skill_row = original
        await self.recover()
        self.assertFalse(self.target.exists())
        self.assertIsNone(await self.storage.get_skill_row(self.storage.created[0]["id"]))

    async def test_delete_crash_restores_before_commit_and_never_revives_after_commit(self):
        row = await self.install()
        original = self.storage.delete_skill_row
        self.storage.delete_skill_row = AsyncMock(side_effect=ProcessStopped())
        with self.assertRaises(ProcessStopped):
            async with self.operations.locked():
                await self.operations.delete(self.storage, row)
        self.assertFalse(self.target.exists())
        self.storage.delete_skill_row = original
        await self.recover()
        self.assertTrue(self.target.exists())

        async def crash_after_commit(skill_id):
            await original(skill_id)
            raise ProcessStopped()

        self.storage.delete_skill_row = crash_after_commit
        with self.assertRaises(ProcessStopped):
            async with self.operations.locked():
                await self.operations.delete(self.storage, row)
        self.storage.delete_skill_row = original
        await self.recover()
        self.assertFalse(self.target.exists())
        self.assertEqual(SkillCatalog(self.target.parent).list(), [])

    async def test_preview_dependencies_are_checks_not_execution(self):
        archive = make_zip(
            {
                "SKILL.md": b"---\nname: demo\ndescription: demo\nmelonclaw_requirements:\n  commands: [melonclaw-command-that-does-not-exist]\n  mcp: [missing]\n  config: [PRIVATE_KEY]\n---\nbody"
            }
        )
        draft = await self.service.prepare(
            user_id="u1", archive_bytes=archive, storage=self.storage
        )
        self.assertEqual(
            [item["status"] for item in draft.preview["dependency_checks"]],
            ["missing", "missing", "manual"],
        )
        self.assertNotIn("PRIVATE_KEY=", str(draft.public_dict()))

    async def test_third_party_requirements_do_not_invalidate_skill(self):
        archive = make_zip(
            {
                "SKILL.md": b"---\nname: demo\ndescription: demo\nrequirements:\n  python: 3.9+\n  env: [TOKEN]\n---\nbody"
            }
        )
        draft = await self.service.prepare(
            user_id="u1", archive_bytes=archive, storage=self.storage
        )
        self.assertEqual(draft.preview["dependency_checks"], [])

    async def test_snapshot_filters_disabled_and_is_immutable(self):
        row = await self.install()
        row["user_enabled"] = None
        self.storage.list_visible_skill_rows = AsyncMock(return_value=[row])
        catalog = SkillCatalog([SkillRoot("user", "/skills-user/", self.target.parent)])
        first, routes, references = await skill_snapshot(self.storage, "u1", self.root, catalog)
        self.assertEqual(references[0]["resource_id"], str(row["id"]))
        snapshot_file = routes[0][1] / "demo/SKILL.md"
        draft = await self.update_draft(row)
        await self.service.confirm(user_id="u1", draft_id=draft.draft_id, storage=self.storage)
        second, _, _ = await skill_snapshot(self.storage, "u1", self.root, catalog)
        self.assertNotEqual(first, second)
        self.assertIn("初版", snapshot_file.read_text())
        row["enabled"] = False
        _, routes, references = await skill_snapshot(self.storage, "u1", self.root, catalog)
        self.assertEqual(routes, ())
        self.assertEqual(references, [])

    async def test_same_name_definitions_and_shadowing_are_scope_specific(self):
        private = await self.install()
        private.update(user_enabled=None)
        shared = {
            **private,
            "id": "shared-id",
            "scope": "global",
            "storage_path": "shared/demo",
            "created_by": "admin",
        }
        shared_dir = self.root / "skills/shared/demo"
        shared_dir.mkdir(parents=True)
        (shared_dir / "SKILL.md").write_text("broken")
        catalog = SkillCatalog(
            [
                SkillRoot("global", "/skills/", shared_dir.parent),
                SkillRoot("user", "/skills-user/", self.target.parent),
            ]
        )
        states = evaluate_skills([shared, private], catalog, self.root / "skills")
        self.assertEqual(states[0].availability, "invalid")
        self.assertEqual(states[0].public_dict()["description"], "")
        (shared_dir / "SKILL.md").write_text("---\nname: demo\ndescription: 共享说明\n---\n# 共享")
        private["enabled"] = False
        states = evaluate_skills([shared, private], catalog, self.root / "skills")
        self.assertFalse(states[0].shadowed)
        self.assertTrue(states[0].effective_enabled)
        self.assertEqual(states[0].public_dict()["description"], "共享说明")
