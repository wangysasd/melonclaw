import tempfile
import unittest
from pathlib import Path

from melonclaw.services.skills import SkillCatalog


class SkillCatalogTests(unittest.TestCase):
    def test_lists_frontmatter_and_uses_first_heading_as_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alpha-skill").mkdir()
            (root / "alpha-skill" / "SKILL.md").write_text(
                "---\n"
                "name: alpha-skill\n"
                "description: Alpha description\n"
                "---\n"
                "# Alpha display name\n",
                encoding="utf-8",
            )
            catalog = SkillCatalog(root)

            items = catalog.list()

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].id, "alpha-skill")
            self.assertEqual(items[0].display_name, "Alpha display name")
            self.assertEqual(items[0].description, "Alpha description")
            self.assertEqual(items[0].virtual_path, "/skills/alpha-skill/SKILL.md")
            self.assertEqual(
                items[0].public_dict(),
                {
                    "id": "global:alpha-skill",
                    "display_name": "Alpha display name",
                    "description": "Alpha description",
                    "scope": "global",
                },
            )

    def test_skips_missing_and_mismatched_skill_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "without-skill-file").mkdir()
            (root / "bad-name").mkdir()
            (root / "bad-name" / "SKILL.md").write_text(
                "---\nname: another-name\ndescription: invalid\n---\n",
                encoding="utf-8",
            )
            (root / ".hidden").mkdir()
            (root / ".hidden" / "SKILL.md").write_text(
                "---\nname: hidden\ndescription: invalid\n---\n",
                encoding="utf-8",
            )

            self.assertEqual(SkillCatalog(root).list(), [])

    def test_humanizes_id_when_skill_has_no_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tushare-fetcher").mkdir()
            (root / "tushare-fetcher" / "SKILL.md").write_text(
                "---\n"
                "name: tushare-fetcher\n"
                "description: Fetch financial data\n"
                "---\n",
                encoding="utf-8",
            )

            items = SkillCatalog(root).list()

            self.assertEqual(items[0].display_name, "Tushare Fetcher")


class _VisibleSkillsStorage:
    """只服务 visible_skills 的替身：按调用者返回预置行。"""

    def __init__(self, rows):
        self.rows = rows

    async def list_visible_skill_rows(self, user_id, *, include_disabled=False):
        return [row for row in self.rows if row["scope"] == "global" or row["created_by"] == user_id]


def _write_skill(root: Path, storage_path: str, name: str) -> None:
    directory = root / storage_path
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 示例\n---\n",
        encoding="utf-8",
    )


class VisibleSkillsShadowTests(unittest.IsolatedAsyncioTestCase):
    """私有与共享同名共存时，私有遮蔽共享，Agent 目录只出现私有那份。"""

    def setUp(self):
        from types import SimpleNamespace

        from melonclaw.services.runtime import ChatRuntime

        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._runtime_cls = ChatRuntime
        self._namespace = SimpleNamespace

    def _row(self, name, scope, created_by):
        return {
            "id": f"id-{scope}-{name}",
            "name": name,
            "scope": scope,
            "source_type": "upload",
            "enabled": True,
            "created_by": created_by,
            "storage_path": (
                f"shared/{name}" if scope == "global" else f"users/{created_by}/{name}"
            ),
            "user_enabled": None,
            "status": "ready", "version": 1, "content_hash": "", "source_url": "", "source_ref": "",
        }

    async def _visible_ids(self, rows, user_id="user-1"):
        runtime = self._runtime_cls()
        runtime.settings = self._namespace(data_root=self.data_root)
        runtime.storage = _VisibleSkillsStorage(rows)
        # 单测不启动完整运行时（连接池、Memory 等），直接顶替就绪检查。
        runtime.require_ready = lambda: runtime.storage
        definitions = await runtime.visible_skills(user_id)
        return {item.id: item for item in definitions}

    async def test_private_skill_shadows_shared_same_name(self):
        _write_skill(self.data_root, "skills/shared/foo", "foo")
        _write_skill(self.data_root, "skills/users/user-1/foo", "foo")
        _write_skill(self.data_root, "skills/shared/bar", "bar")
        rows = [
            self._row("foo", "global", "admin-1"),
            self._row("foo", "user", "user-1"),
            self._row("bar", "global", "admin-1"),
        ]

        found = await self._visible_ids(rows)

        self.assertEqual(set(found), {"foo", "bar"})
        self.assertTrue(found["foo"].virtual_path.startswith("/skills-user/"))
        self.assertTrue(found["bar"].virtual_path.startswith("/skills/"))

    async def test_shared_skill_visible_when_no_private_same_name(self):
        _write_skill(self.data_root, "skills/shared/foo", "foo")
        rows = [self._row("foo", "global", "admin-1")]

        found = await self._visible_ids(rows)

        self.assertIn("foo", found)
        self.assertTrue(found["foo"].virtual_path.startswith("/skills/"))

    async def test_private_skill_does_not_expose_unlisted_shared_copy(self):
        """共享目录仍在但共享行停用时，同名私有行只能放行私有路径。"""
        _write_skill(self.data_root, "skills/shared/foo", "foo")
        _write_skill(self.data_root, "skills/users/user-1/foo", "foo")
        runtime = self._runtime_cls()
        runtime.settings = self._namespace(data_root=self.data_root)
        runtime.storage = _VisibleSkillsStorage([self._row("foo", "user", "user-1")])
        runtime.require_ready = lambda: runtime.storage

        definitions = await runtime.visible_skills("user-1")

        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0].scope, "user")

    async def test_other_users_private_name_does_not_shadow_shared(self):
        """user-2 的私有 foo 不影响 user-1 看到共享 foo。"""
        _write_skill(self.data_root, "skills/shared/foo", "foo")
        _write_skill(self.data_root, "skills/users/user-2/foo", "foo")
        rows = [
            self._row("foo", "global", "admin-1"),
            self._row("foo", "user", "user-2"),
        ]

        found = await self._visible_ids(rows, user_id="user-1")

        self.assertIn("foo", found)
        self.assertTrue(found["foo"].virtual_path.startswith("/skills/"))


if __name__ == "__main__":
    unittest.main()
