"""Skill ZIP 两段式导入的校验与状态机。"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from melonclaw.services.skill_import import SkillImportError, SkillImportService


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


VALID_SKILL_MD = (
    "---\nname: my-skill\ndescription: 我的技能\n---\n# My Skill\n正文\n"
)


class FakeStorage:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.updated = []
        self.deleted = []

    async def get_skill_row(self, name):
        return {"name": name} if name in self.existing else None

    async def create_skill_row(self, **kwargs):
        self.created.append(kwargs)
        return kwargs

    async def update_skill_row(self, name, **fields):
        self.updated.append((name, fields))

    async def delete_skill_row(self, name):
        self.deleted.append(name)


class SkillImportServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    async def test_prepare_with_wrapper_directory_and_confirm(self):
        service = SkillImportService(self.data_root)
        storage = FakeStorage()
        archive = make_zip(
            {
                "my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8"),
                "my-skill/scripts/run.py": b"print('hi')\n",
            }
        )

        draft = await service.prepare(
            user_id="u1", archive_bytes=archive, storage=storage
        )

        self.assertEqual(draft.name, "my-skill")
        self.assertEqual(draft.file_count, 2)
        result = await service.confirm(
            draft_id=draft.draft_id, user_id="u1", storage=storage
        )
        self.assertEqual(result.name, "my-skill")
        target = self.data_root / "skills" / "users" / "u1" / "my-skill"
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((target / "scripts" / "run.py").is_file())
        self.assertEqual(storage.created[0]["scope"], "user")
        self.assertEqual(storage.created[0]["enabled"], False)
        self.assertEqual(storage.updated[-1][0], "my-skill")
        self.assertEqual(storage.updated[-1][1]["enabled"], True)
        self.assertFalse((self.data_root / "skills" / "tmp").joinpath(draft.draft_id).exists())

    async def test_prepare_accepts_flat_layout(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        draft = await service.prepare(
            user_id="u1", archive_bytes=archive, storage=FakeStorage()
        )

        self.assertEqual(draft.name, "my-skill")

    async def test_rejects_path_traversal(self):
        service = SkillImportService(self.data_root)
        archive = make_zip(
            {
                "my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8"),
                "my-skill/../evil.txt": b"x",
            }
        )

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="u1", archive_bytes=archive, storage=FakeStorage()
            )

    async def test_rejects_absolute_and_backslash_paths(self):
        service = SkillImportService(self.data_root)
        for bad_name in ("/abs/path.txt", "..\\..\\evil.txt"):
            with self.subTest(name=bad_name):
                archive = make_zip({bad_name: b"x"})
                with self.assertRaises(SkillImportError):
                    await service.prepare(
                        user_id="u1", archive_bytes=archive, storage=FakeStorage()
                    )

    async def test_rejects_invalid_frontmatter(self):
        service = SkillImportService(self.data_root)
        archive = make_zip(
            {
                "some-dir/SKILL.md": (
                    "---\nname: my-skill\n---\n# 缺 description\n"
                ).encode("utf-8")
            }
        )

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="u1", archive_bytes=archive, storage=FakeStorage()
            )

    async def test_rejects_duplicate_name(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="u1",
                archive_bytes=archive,
                storage=FakeStorage(existing=["my-skill"]),
            )

    async def test_rejects_zip_bomb_compression_ratio(self):
        service = SkillImportService(self.data_root)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("my-skill/SKILL.md", VALID_SKILL_MD.encode("utf-8"))
            archive.writestr("my-skill/payload.bin", b"\0" * (5 * 1024 * 1024))
        buffer.seek(0)

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="u1", archive_bytes=buffer.getvalue(), storage=FakeStorage()
            )

    async def test_cancel_removes_draft_and_tmp(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})
        draft = await service.prepare(
            user_id="u1", archive_bytes=archive, storage=FakeStorage()
        )

        await service.cancel(draft_id=draft.draft_id, user_id="u1")

        self.assertFalse(
            (self.data_root / "skills" / "tmp" / draft.draft_id).exists()
        )
        with self.assertRaises(SkillImportError):
            await service.confirm(
                draft_id=draft.draft_id, user_id="u1", storage=FakeStorage()
            )

    async def test_confirm_rejects_other_users_draft(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})
        draft = await service.prepare(
            user_id="u1", archive_bytes=archive, storage=FakeStorage()
        )

        with self.assertRaises(SkillImportError):
            await service.confirm(
                draft_id=draft.draft_id, user_id="u2", storage=FakeStorage()
            )


if __name__ == "__main__":
    unittest.main()
