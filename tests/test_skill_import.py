"""Skill ZIP 两段式导入的校验与状态机。"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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
    """existing 接受行 dict 或名字简写（名字简写视为 u1 的私有 Skill）。"""

    def __init__(self, existing=()):
        self.existing = [
            item
            if isinstance(item, dict)
            else {"name": item, "scope": "user", "created_by": "u1"}
            for item in existing
        ]
        self.created = []
        self.updated = []
        self.deleted = []

    async def get_user_context(self, user_id):
        return SimpleNamespace(tenant_role="member")

    async def list_skill_rows_by_name(self, name):
        return [row for row in self.existing + self.created if row["name"] == name and str(row.get("id")) not in self.deleted]

    async def get_skill_row(self, skill_id):
        return next((row for row in self.existing + self.created if str(row["id"]) == str(skill_id) and str(row["id"]) not in self.deleted), None)

    async def list_visible_mcp_rows(self, user_id):
        return []

    async def create_skill_row(self, **kwargs):
        kwargs["id"] = kwargs.pop("skill_id", uuid4())
        kwargs.setdefault("version", 1)
        self.created.append(kwargs)
        return kwargs

    async def update_skill_row(self, skill_id, **fields):
        self.updated.append((skill_id, fields))
        row = await self.get_skill_row(skill_id)
        if row:
            row.update(fields)

    async def delete_skill_row(self, skill_id):
        self.deleted.append(str(skill_id))


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
        self.assertEqual(storage.created[0]["status"], "ready")
        self.assertFalse((self.data_root / "skills" / "tmp").joinpath(draft.draft_id).exists())

    async def test_macos_metadata_is_not_installed(self):
        for index, prefix in enumerate(("", "my-skill/", "./my-skill/")):
            with self.subTest(prefix=prefix):
                root = self.data_root / str(index)
                service = SkillImportService(root)
                storage = FakeStorage()
                draft = await service.prepare(
                    user_id="u1",
                    archive_bytes=make_zip({
                        prefix + "SKILL.md": VALID_SKILL_MD.encode(),
                        prefix + "agents/openai.yaml": b"name: example",
                        "__MACOSX/._my-skill": b"metadata",
                        "__MACOSX/my-skill/._SKILL.md": b"metadata",
                        ".DS_Store": b"metadata",
                        prefix + "agents/.DS_Store": b"metadata",
                        prefix + "agents/._openai.yaml": b"metadata",
                    }),
                    storage=storage,
                )
                self.assertEqual(draft.file_count, 2)
                await service.confirm(
                    draft_id=draft.draft_id, user_id="u1", storage=storage
                )
                target = root / "skills/users/u1/my-skill"
                self.assertEqual(
                    sorted(str(p.relative_to(target)) for p in target.rglob("*")
                           if p.is_file()),
                    ["SKILL.md", "agents/openai.yaml"],
                )

    async def test_rejects_metadata_only_and_ambiguous_layouts(self):
        for entries in (
            {"__MACOSX/._skill": b"metadata", ".DS_Store": b"metadata"},
            {"one/SKILL.md": VALID_SKILL_MD.encode(),
             "two/SKILL.md": VALID_SKILL_MD.encode()},
            {"outer/inner/SKILL.md": VALID_SKILL_MD.encode()},
        ):
            with self.subTest(entries=list(entries)):
                with self.assertRaises(SkillImportError):
                    await SkillImportService(self.data_root).prepare(
                        user_id="u1", archive_bytes=make_zip(entries),
                        storage=FakeStorage(),
                    )

    async def test_metadata_does_not_bypass_safety_checks(self):
        for filename, content in (
            ("__MACOSX/../._escape", b"metadata"),
            ("/__MACOSX/._absolute", b"metadata"),
            ("__MACOSX/._large", b"x" * 101),
        ):
            with self.subTest(filename=filename):
                with patch("melonclaw.services.skill_import.MAX_SKILL_FILE_SIZE", 100):
                    with self.assertRaises(SkillImportError):
                        await SkillImportService(self.data_root).prepare(
                            user_id="u1",
                            archive_bytes=make_zip({
                                "my-skill/SKILL.md": VALID_SKILL_MD.encode(),
                                filename: content,
                            }),
                            storage=FakeStorage(),
                        )

    async def test_install_scope_comes_from_current_database_role(self):
        for role in ("admin", "owner", "member"):
            for source_type in ("upload", "remote"):
                with self.subTest(role=role, source_type=source_type):
                    root = self.data_root / role / source_type
                    service = SkillImportService(root)
                    storage = FakeStorage()
                    storage.get_user_context = AsyncMock(return_value=SimpleNamespace(tenant_role=role))
                    draft = await service.prepare(
                        user_id="u1",
                        archive_bytes=make_zip({"SKILL.md": VALID_SKILL_MD.encode()}),
                        storage=storage,
                        source_type=source_type,
                    )
                    storage.get_user_context = AsyncMock(
                        return_value=SimpleNamespace(tenant_role=role)
                    )
                    await service.confirm(
                        draft_id=draft.draft_id, user_id="u1", storage=storage
                    )
                    shared = role in {"admin", "owner"}
                    path = "shared/my-skill" if shared else "users/u1/my-skill"
                    self.assertEqual(storage.created[0]["scope"], "global" if shared else "user")
                    self.assertEqual(storage.created[0]["storage_path"], path)
                    self.assertEqual(storage.created[0]["source_type"], source_type)
                    self.assertTrue((root / "skills" / path / "SKILL.md").is_file())
                    self.assertEqual(storage.created[0]["enabled"], False)
                    self.assertEqual(storage.created[0]["status"], "ready")

    async def test_confirm_rejects_invalid_user_before_writing(self):
        service = SkillImportService(self.data_root)
        storage = FakeStorage()
        draft = await service.prepare(
            user_id="u1", archive_bytes=make_zip({"SKILL.md": VALID_SKILL_MD.encode()}),
            storage=storage,
        )
        storage.get_user_context = AsyncMock(return_value=None)
        with self.assertRaises(SkillImportError):
            await service.confirm(draft_id=draft.draft_id, user_id="u1", storage=storage)
        self.assertEqual(storage.created, [])
        self.assertFalse((self.data_root / "skills" / "shared").exists())

    async def test_shared_install_failure_removes_row_and_files(self):
        service = SkillImportService(self.data_root)
        storage = FakeStorage()
        storage.get_user_context = AsyncMock(return_value=SimpleNamespace(tenant_role="admin"))
        draft = await service.prepare(
            user_id="u1", archive_bytes=make_zip({"SKILL.md": VALID_SKILL_MD.encode()}),
            storage=storage,
        )
        with (
            patch(
                "melonclaw.services.skill_operations.Path.rename",
                side_effect=RuntimeError("write failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            await service.confirm(draft_id=draft.draft_id, user_id="u1", storage=storage)
        self.assertEqual(storage.deleted, [str(storage.created[0]["id"])])
        self.assertFalse((self.data_root / "skills" / "shared" / "my-skill").exists())

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

    async def test_rejects_own_duplicate_name(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="u1",
                archive_bytes=archive,
                storage=FakeStorage(existing=["my-skill"]),
            )

    async def test_member_rejects_name_colliding_with_shared_skill(self):
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        with self.assertRaisesRegex(SkillImportError, "共享技能重名"):
            await service.prepare(
                user_id="u1",
                archive_bytes=archive,
                storage=FakeStorage(
                    existing=[
                        {"name": "my-skill", "scope": "global",
                         "created_by": "admin", "enabled": True}
                    ]
                ),
            )

    async def test_member_allows_name_of_globally_disabled_shared_skill(self):
        """全员停用的共享 Skill 用户看不见，允许安装同名私有 Skill；
        管理员重新启用后由私有遮蔽共享的既有语义接管。"""
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        draft = await service.prepare(
            user_id="u1",
            archive_bytes=archive,
            storage=FakeStorage(
                existing=[
                    {
                        "name": "my-skill",
                        "scope": "global",
                        "created_by": "admin",
                        "enabled": False,
                    }
                ]
            ),
        )
        self.assertEqual(draft.name, "my-skill")

    async def test_other_users_private_skill_does_not_block(self):
        """不同用户的私有 Skill 允许同名。"""
        service = SkillImportService(self.data_root)
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        draft = await service.prepare(
            user_id="u1",
            archive_bytes=archive,
            storage=FakeStorage(
                existing=[
                    {"name": "my-skill", "scope": "user", "created_by": "u2"}
                ]
            ),
        )
        self.assertEqual(draft.name, "my-skill")

    async def test_admin_shared_install_not_blocked_by_private_occupant(self):
        """个人占坑不影响全局：管理员上传共享技能不受任何私有同名占用阻挡。"""
        service = SkillImportService(self.data_root)
        storage = FakeStorage(
            existing=[{"name": "my-skill", "scope": "user", "created_by": "u2"}]
        )
        storage.get_user_context = AsyncMock(
            return_value=SimpleNamespace(tenant_role="admin")
        )
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        draft = await service.prepare(
            user_id="admin-1", archive_bytes=archive, storage=storage
        )
        self.assertEqual(draft.name, "my-skill")

    async def test_admin_rejects_duplicate_shared_name(self):
        service = SkillImportService(self.data_root)
        storage = FakeStorage(
            existing=[{"name": "my-skill", "scope": "global", "created_by": "a1"}]
        )
        storage.get_user_context = AsyncMock(
            return_value=SimpleNamespace(tenant_role="admin")
        )
        archive = make_zip({"my-skill/SKILL.md": VALID_SKILL_MD.encode("utf-8")})

        with self.assertRaises(SkillImportError):
            await service.prepare(
                user_id="admin-1", archive_bytes=archive, storage=storage
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
