"""Skill 两层启停约定：个人开关（共享=个人偏好，私有=创建者）+ 管理员全员开关。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from melonclaw.services.resource_service import (
    ResourceNotFoundError,
    ResourcePermissionError,
    ResourceService,
    SkillStateError,
)
from melonclaw.services.skills import (
    GLOBAL_SKILLS_ROUTE,
    USER_SKILLS_ROUTE,
    SkillCatalog,
    SkillRoot,
)


def make_row(**overrides):
    row = {
        "name": "my-skill",
        "scope": "user",
        "source_type": "upload",
        "enabled": True,
        "created_by": "user-1",
        "storage_path": "users/user-1/my-skill",
    }
    row.update(overrides)
    return row


class FakeStorage:
    def __init__(self, row):
        self.row = row
        self.updated = []
        self.user_states = []

    async def get_user_context(self, user_id):
        roles = {"admin-1": "admin"}
        return SimpleNamespace(tenant_role=roles.get(user_id, "member"))

    async def get_skill_row(self, name):
        return self.row if self.row and self.row["name"] == name else None

    async def update_skill_row(self, name, **fields):
        self.updated.append((name, fields))

    async def set_skill_user_state(self, user_id, skill_name, enabled):
        self.user_states.append((user_id, skill_name, enabled))


def make_service(row):
    storage = FakeStorage(row)
    runtime = SimpleNamespace(
        require_ready=lambda: storage,
        settings=SimpleNamespace(data_root=None),
    )
    return ResourceService(runtime), storage


class SkillPersonalToggleTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_toggle_writes_personal_state_not_row(self):
        """共享 Skill 的个人开关写 user state，不动 skills.enabled。"""
        service, storage = make_service(make_row(scope="global"))
        await service.set_skill_enabled("user-1", "my-skill", False)
        assert storage.user_states == [("user-1", "my-skill", False)]
        assert storage.updated == []

    async def test_global_toggle_by_admin_is_personal_too(self):
        """管理员用个人开关停共享 Skill 也只影响自己，不等于全员停用。"""
        service, storage = make_service(make_row(scope="global"))
        await service.set_skill_enabled("admin-1", "my-skill", False)
        assert storage.user_states == [("admin-1", "my-skill", False)]
        assert storage.updated == []

    async def test_own_private_skill_can_be_toggled(self):
        service, storage = make_service(make_row())
        await service.set_skill_enabled("user-1", "my-skill", False)
        assert storage.updated == [("my-skill", {"enabled": False})]

    async def test_others_private_skill_cannot_be_toggled(self):
        service, _ = make_service(make_row())
        with self.assertRaises(ResourcePermissionError):
            await service.set_skill_enabled("user-2", "my-skill", False)

    async def test_missing_skill_is_404(self):
        service, _ = make_service(None)
        with self.assertRaises(ResourceNotFoundError):
            await service.set_skill_enabled("admin-1", "nope", False)


class SkillGlobalStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_toggle_global_state(self):
        service, storage = make_service(make_row(scope="global"))
        await service.set_skill_global_enabled("admin-1", "my-skill", False)
        assert storage.updated == [("my-skill", {"enabled": False})]
        assert storage.user_states == []

    async def test_member_cannot_toggle_global_state(self):
        service, storage = make_service(make_row(scope="global"))
        with self.assertRaises(ResourcePermissionError):
            await service.set_skill_global_enabled("user-1", "my-skill", False)
        assert storage.updated == []

    async def test_private_skill_rejects_global_state(self):
        service, storage = make_service(make_row())
        with self.assertRaises(SkillStateError):
            await service.set_skill_global_enabled("admin-1", "my-skill", False)
        assert storage.updated == []

    async def test_missing_skill_is_404(self):
        service, _ = make_service(None)
        with self.assertRaises(ResourceNotFoundError):
            await service.set_skill_global_enabled("admin-1", "nope", False)


class SkillPublishTests(unittest.IsolatedAsyncioTestCase):
    """发布前必须确认目录还在：否则 FileNotFoundError 会带着宿主机绝对路径
    冒到 API 边界，而 sanitize_text 只脱敏密钥、不管文件路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def own_private_row(self):
        """发布需要同时满足「是 admin」和「是自己的私有技能」。"""

        return make_row(created_by="admin-1", storage_path="users/admin-1/my-skill")

    def make_service(self, row):
        storage = FakeStorage(row)
        runtime = SimpleNamespace(
            require_ready=lambda: storage,
            settings=SimpleNamespace(data_root=self.data_root),
        )
        return ResourceService(runtime), storage

    def write_skill_dir(self, storage_path):
        directory = self.data_root / "skills" / storage_path
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        return directory

    async def test_publish_without_directory_is_404_and_touches_nothing(self):
        service, storage = self.make_service(self.own_private_row())

        with self.assertRaises(ResourceNotFoundError) as raised:
            await service.publish_skill("admin-1", "my-skill")

        assert raised.exception.status_code == 404
        assert storage.updated == []  # 检查在改行之前，行没被动过
        assert str(self.data_root) not in str(raised.exception)

    async def test_publish_moves_directory_and_row(self):
        service, storage = self.make_service(self.own_private_row())
        source = self.write_skill_dir("users/admin-1/my-skill")

        await service.publish_skill("admin-1", "my-skill")

        target = self.data_root / "skills" / "shared" / "my-skill"
        assert target.is_dir()
        assert not source.exists()
        assert storage.updated == [
            (
                "my-skill",
                {
                    "scope": "global",
                    "storage_path": "shared/my-skill",
                    "version": 2,
                },
            )
        ]


class RowsStorage:
    """按行返回可管理 Skill 的替身，供 availability 判定使用。"""

    def __init__(self, rows, role="owner"):
        self.rows = rows
        self.role = role

    async def get_user_context(self, user_id):
        return SimpleNamespace(tenant_role=self.role)

    async def list_visible_skill_rows(self, user_id, *, include_disabled=False):
        return self.rows


class SkillAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    """行还在、磁盘上读不出来时，管理页要能分清「目录没了」和「文件坏了」。

    两者该做的动作相反：目录没了只能删行，文件坏了修好就能用。合成一个
    布尔会把用户引去删一个其实还在的技能。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.skills_root = self.data_root / "skills"
        self.addCleanup(self._tmp.cleanup)

    def write_skill(self, storage_path, name, body=None):
        directory = self.skills_root / storage_path
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            body
            if body is not None
            else f"---\nname: {name}\ndescription: 示例\n---\n# {name}\n",
            encoding="utf-8",
        )

    def row(self, name, storage_path):
        return {
            "name": name,
            "scope": "user",
            "source_type": "upload",
            "enabled": True,
            "created_by": "user-1",
            "storage_path": storage_path,
            "user_enabled": None,
        }

    def make_service(self, rows):
        storage = RowsStorage(rows)
        skills_root = self.skills_root
        runtime = SimpleNamespace(
            require_ready=lambda: storage,
            settings=SimpleNamespace(data_root=self.data_root),
            _catalog_for=lambda user_id: SkillCatalog(
                [
                    SkillRoot(
                        scope="global",
                        route_prefix=GLOBAL_SKILLS_ROUTE,
                        directory=skills_root / "shared",
                    ),
                    SkillRoot(
                        scope="user",
                        route_prefix=USER_SKILLS_ROUTE,
                        directory=skills_root / "users" / user_id,
                    ),
                ]
            ),
        )
        return ResourceService(runtime)

    async def test_ready_skill_is_reported_ready(self):
        self.write_skill("users/user-1/ok-skill", "ok-skill")
        service = self.make_service([self.row("ok-skill", "users/user-1/ok-skill")])

        items = (await service.manageable_skills("user-1"))["items"]

        assert items[0]["availability"] == "ready"
        assert items[0]["description"] == "示例"

    async def test_row_without_directory_is_missing(self):
        service = self.make_service([self.row("gone-skill", "users/user-1/gone-skill")])

        items = (await service.manageable_skills("user-1"))["items"]

        assert items[0]["availability"] == "missing"

    async def test_directory_with_unreadable_skill_is_invalid(self):
        """目录还在、只是 SKILL.md 解析不了：修好文件就能用，不该说成丢失。"""

        self.write_skill(
            "users/user-1/broken-skill", "broken-skill", body="没有 frontmatter"
        )
        service = self.make_service(
            [self.row("broken-skill", "users/user-1/broken-skill")]
        )

        items = (await service.manageable_skills("user-1"))["items"]

        assert items[0]["availability"] == "invalid"

    async def test_name_mismatch_counts_as_invalid_not_missing(self):
        """name 与目录名不一致时目录仍在磁盘上，同样按 invalid 报。"""

        self.write_skill(
            "users/user-1/mismatch",
            "mismatch",
            body="---\nname: other\ndescription: 示例\n---\n",
        )
        service = self.make_service([self.row("mismatch", "users/user-1/mismatch")])

        items = (await service.manageable_skills("user-1"))["items"]

        assert items[0]["availability"] == "invalid"


if __name__ == "__main__":
    unittest.main()
