"""admin 创建用户的权限与校验。"""

import unittest
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from melonclaw.repository.users import UserRepositoryMixin
from melonclaw.services.chat import ChatService
from melonclaw.services.resource_service import ResourcePermissionError


class FakeStorage:
    def __init__(self):
        self.created = []

    async def user_exists(self, user_id):
        return any(row["user_id"] == user_id for row in self.created)

    async def create_user(self, **fields):
        self.created.append(fields)


def make_service(storage, actor_role="owner"):
    async def resolve_user(user_id):
        return SimpleNamespace(
            user_id=user_id,
            tenant_role=actor_role,
            tenant_status="active",
        )

    runtime = SimpleNamespace(require_ready=lambda: storage)
    conversations = SimpleNamespace(resolve_user=resolve_user)
    # ChatService.__init__ 需要多个协作对象；创建用户只用到 conversations/runtime。
    service = ChatService.__new__(ChatService)
    service.runtime = runtime
    service.conversations = conversations
    return service


class CreateUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_creates_member_in_system_tenant(self):
        storage = FakeStorage()
        await make_service(storage).create_user("admin", "zhangsan", "张三")
        self.assertEqual(
            storage.created,
            [
                {
                    "user_id": "zhangsan",
                    "user_name_zh": "张三",
                    "tenant_id": "system",
                    "tenant_role": "member",
                }
            ],
        )

    async def test_member_cannot_create_user(self):
        with self.assertRaises(ResourcePermissionError):
            await make_service(FakeStorage(), actor_role="member").create_user(
                "zhangsan", "lisi", "李四"
            )

    async def test_rejects_bad_user_id(self):
        with self.assertRaises(ValueError):
            await make_service(FakeStorage()).create_user("admin", "Zhang San", "张三")

    async def test_rejects_duplicate(self):
        storage = FakeStorage()
        service = make_service(storage)
        await service.create_user("admin", "zhangsan", "张三")
        with self.assertRaises(ValueError):
            await service.create_user("admin", "zhangsan", "三")

    async def test_rejects_long_name(self):
        with self.assertRaises(ValueError):
            await make_service(FakeStorage()).create_user("admin", "longname", "张三四五")


class RepositoryDuplicateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_integrity_error_mapped_to_value_error(self):
        """并发创建撞主键时，DB 唯一约束兜底并转成可读的 422。"""

        class Connection:
            async def execute(self, *_args):
                raise IntegrityError("INSERT INTO users ...", {}, Exception("dup"))

        class Transaction:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, *_args):
                return False

        mixin = UserRepositoryMixin()
        mixin.engine = SimpleNamespace(begin=lambda: Transaction())
        with self.assertRaises(ValueError) as caught:
            await mixin.create_user(
                user_id="zhangsan", user_name_zh="张三", tenant_id="system"
            )
        self.assertIn("已存在", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
