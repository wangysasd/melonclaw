"""Skill 磁盘索引重建：清库后能从 data_root 补回索引，且不覆盖已有运营状态。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from melonclaw.services.skill_index import reindex_skills_from_disk


def write_skill(directory: Path, name: str, description: str = "示例技能") -> Path:
    skill_dir = directory / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


class FakeStorage:
    """只实现重建用到的方法：登记（幂等）+ 用户存在性。"""

    def __init__(self, users):
        self.users = set(users)
        self.rows: dict[str, dict] = {}
        self.calls: list[dict] = []

    async def user_exists(self, user_id: str) -> bool:
        return user_id in self.users

    async def create_skill_row_if_missing(self, **fields) -> bool:
        self.calls.append(fields)
        name = fields["name"]
        if name in self.rows:
            return False
        self.rows[name] = fields
        return True

    async def list_all_skill_rows(self) -> list[dict]:
        return [dict(row) for row in self.rows.values()]


class SkillReindexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.skills_root = self.data_root / "skills"
        self.addCleanup(self._tmp.cleanup)

    def seed_disk(self):
        write_skill(self.skills_root / "shared", "shared-a")
        write_skill(self.skills_root / "shared", "shared-b")
        write_skill(self.skills_root / "users" / "u1", "private-a")
        # 没有 SKILL.md 的目录不应被登记。
        (self.skills_root / "shared" / "no-body").mkdir(parents=True)
        # 草稿区不属于任何 scope。
        write_skill(self.skills_root / "tmp", "draft-skill")
        # 用户目录存在但数据库没有该用户。
        write_skill(self.skills_root / "users" / "ghost", "ghost-skill")

    async def test_registers_shared_and_user_skills(self):
        self.seed_disk()
        storage = FakeStorage(users=["u1"])

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert set(report.registered) == {"shared-a", "shared-b", "private-a"}
        assert report.orphaned == ("ghost/ghost-skill",)
        shared = storage.rows["shared-a"]
        assert shared["scope"] == "global"
        assert shared["source_type"] == "builtin"
        assert shared["created_by"] == "admin"
        assert shared["storage_path"] == "shared/shared-a"
        private = storage.rows["private-a"]
        assert private["scope"] == "user"
        assert private["source_type"] == "upload"
        assert private["created_by"] == "u1"
        assert private["storage_path"] == "users/u1/private-a"

    async def test_directories_without_valid_skill_are_ignored(self):
        self.seed_disk()
        storage = FakeStorage(users=["u1"])

        await reindex_skills_from_disk(storage, self.data_root)

        assert "no-body" not in storage.rows
        assert "draft-skill" not in storage.rows
        assert "ghost-skill" not in storage.rows

    async def test_reindex_is_idempotent_and_never_overwrites(self):
        self.seed_disk()
        storage = FakeStorage(users=["u1"])
        await reindex_skills_from_disk(storage, self.data_root)

        # 模拟使用者改过运营状态：共享项被全员停用、另一项索引范围被调整
        # 并调整了索引目录；重建不应覆盖现有记录。
        storage.rows["shared-a"]["enabled"] = False
        storage.rows["private-a"]["scope"] = "global"
        storage.rows["private-a"]["storage_path"] = "shared/private-a"
        write_skill(self.skills_root / "shared", "private-a")
        shutil.rmtree(self.skills_root / "users" / "u1" / "private-a")

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert report.registered == ()
        assert report.missing == ()
        assert report.changed is False
        assert set(report.already_indexed) == {
            "shared-a",
            "shared-b",
            "private-a",
        }
        assert storage.rows["shared-a"]["enabled"] is False
        assert storage.rows["private-a"]["scope"] == "global"
        assert storage.rows["private-a"]["storage_path"] == "shared/private-a"

    async def test_missing_data_root_is_not_an_error(self):
        storage = FakeStorage(users=[])

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert report.registered == ()
        assert report.orphaned == ()
        assert report.missing == ()
        assert storage.calls == []

    async def test_row_without_directory_is_reported_but_kept(self):
        self.seed_disk()
        storage = FakeStorage(users=["u1"])
        await reindex_skills_from_disk(storage, self.data_root)

        # 模拟目录被手工删除：行还在，磁盘上没了。
        shutil.rmtree(self.skills_root / "shared" / "shared-b")

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert report.missing == ("shared-b",)
        assert report.changed is True
        assert "shared-b" in storage.rows  # 只报告，不删行

    async def test_directory_present_but_invalid_is_not_reported_missing(self):
        """目录还在、只是 SKILL.md 坏了时不能算“目录丢失”。

        扫描会跳过校验不过的目录，若按“扫描结果里没有”判丢失，就会删掉
        一行好数据并级联清空全员的个人偏好。这里钉住“按目录是否存在判断”。
        """

        self.seed_disk()
        storage = FakeStorage(users=["u1"])
        await reindex_skills_from_disk(storage, self.data_root)

        before = dict(storage.rows["shared-b"])
        broken = self.skills_root / "shared" / "shared-b" / "SKILL.md"
        broken.write_text("没有 frontmatter 的正文", encoding="utf-8")

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert report.missing == ()
        assert storage.rows["shared-b"] == before  # 没被删，也没被改写

    async def test_user_directory_without_user_row_is_skipped(self):
        write_skill(self.skills_root / "users" / "ghost", "ghost-skill")
        storage = FakeStorage(users=[])

        report = await reindex_skills_from_disk(storage, self.data_root)

        assert report.registered == ()
        assert report.orphaned == ("ghost/ghost-skill",)
        assert storage.calls == []


if __name__ == "__main__":
    unittest.main()
