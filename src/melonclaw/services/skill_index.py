"""Skill 磁盘索引重建：把数据根里已有的 Skill 目录补登记进数据库索引。

可见性公式是「磁盘扫描 ∩ 数据库可见行」（``services/runtime.py``），而数据库
索引没有任何种子或迁移路径——写操作（导入、删除）都是同时改目录和行，
所以行一旦随数据库清空而丢失，目录还在、索引却是空的，**全部共享 Skill 会从
选择器和执行白名单里静默消失**。这里提供从磁盘（SKILL.md 的唯一事实来源）
重建索引的入口，符合「文件系统是正文唯一事实来源，数据库只是索引」的定位。

只补缺、不覆盖：已有行代表使用者通过资源管理 API 维护过的运营状态
（启用/停用等），重建过程绝不改动它们。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from melonclaw.repository import BusinessRepository
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skills import SAFE_DIRECTORY_RE, SkillCatalog, read_skill

logger = logging.getLogger(__name__)

SHARED_SCOPE_DIRECTORY = "shared"
USERS_SCOPE_DIRECTORY = "users"


@dataclass(frozen=True)
class SkillReindexReport:
    """一次索引重建的结果，供调用方输出摘要。"""

    registered: tuple[str, ...] = ()
    already_indexed: tuple[str, ...] = ()
    orphaned: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.registered) or bool(self.missing)

    def summary(self) -> str:
        return (
            f"Skill 索引重建：补登记 {len(self.registered)} 项，"
            f"已存在 {len(self.already_indexed)} 项，"
            f"无法登记 {len(self.orphaned)} 项，"
            f"目录已丢失 {len(self.missing)} 项，文件异常 {len(self.invalid)} 项。"
        )


def _scan(directory: Path) -> list[str]:
    """扫描一个 Skill 根，返回通过既定校验的 Skill 名。

    复用 ``SkillCatalog``：SKILL.md 必须存在、frontmatter 可解析、
    name 与目录名一致、目录名安全。没有合法 SKILL.md 的目录不会被登记，
    因此不会往索引里塞运行时不认识的条目。
    """

    if not directory.is_dir():
        return []
    return [definition.id for definition in SkillCatalog(directory).list()]


def _subdirectories(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_dir()
            and not child.is_symlink()
            and SAFE_DIRECTORY_RE.fullmatch(child.name)
        ),
        key=lambda item: item.name,
    )


async def _reindex_skills_from_disk(
    storage: BusinessRepository, data_root: Path
) -> SkillReindexReport:
    """把 ``<data_root>/skills`` 下已有的目录补登记进 ``skills`` 表。

    - ``shared/<name>``      → ``scope='global'``，归属种子管理员，视为内置；
    - ``users/<uid>/<name>`` → ``scope='user'``，归属该用户，视为上传。

    用户目录仅在其用户行存在时登记（``created_by`` 有外键约束）；找不到
    用户的目录记入 ``orphaned`` 并打 warning，不会中断重建。

    反方向的“有行无目录”只报告不删除（``missing``）：这类行不会出现在
    选择器里，但也不会自己消失，不报出来就完全不可见。
    """

    skills_root = data_root / "skills"
    registered: list[str] = []
    already_indexed: list[str] = []
    orphaned: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []

    async def register(
        *,
        name: str,
        scope: str,
        source_type: str,
        created_by: str,
        storage_path: str,
    ) -> None:
        created = await storage.create_skill_row_if_missing(
            name=name,
            scope=scope,
            source_type=source_type,
            created_by=created_by,
            storage_path=storage_path,
        )
        if created:
            registered.append(name)
        else:
            already_indexed.append(name)

    for name in _scan(skills_root / SHARED_SCOPE_DIRECTORY):
        await register(
            name=name,
            scope="global",
            source_type="builtin",
            created_by=DEFAULT_SIMULATED_USER_ID,
            storage_path=f"{SHARED_SCOPE_DIRECTORY}/{name}",
        )

    users_root = skills_root / USERS_SCOPE_DIRECTORY
    for user_dir in _subdirectories(users_root):
        user_id = user_dir.name
        if not await storage.user_exists(user_id):
            names = _scan(user_dir)
            orphaned.extend(f"{user_id}/{name}" for name in names)
            if names:
                logger.warning(
                    "跳过 %d 个 Skill：数据根有用户目录 %r 但数据库无该用户。",
                    len(names),
                    user_id,
                )
            continue
        for name in _scan(user_dir):
            await register(
                name=name,
                scope="user",
                source_type="upload",
                created_by=user_id,
                storage_path=f"{USERS_SCOPE_DIRECTORY}/{user_id}/{name}",
            )

    for row in await storage.list_all_skill_rows():
        if not (skills_root / str(row["storage_path"])).is_dir():
            missing.append(str(row["name"]))
        else:
            try:
                read_skill(skills_root / row["storage_path"])
            except (ValueError, OSError):
                invalid.append(f"{row['scope']}:{row['name']}")
    if missing:
        logger.warning(
            "有 %d 项 Skill 索引指向的目录已丢失：%s。这些项不会出现在选择器里，"
            "可在资源管理界面删除，或把目录恢复回原位。",
            len(missing),
            "、".join(missing),
        )

    return SkillReindexReport(
        registered=tuple(registered),
        already_indexed=tuple(already_indexed),
        orphaned=tuple(orphaned),
        missing=tuple(missing),
        invalid=tuple(invalid),
    )


__all__ = [
    "SHARED_SCOPE_DIRECTORY",
    "SkillReindexReport",
    "USERS_SCOPE_DIRECTORY",
    "reindex_skills_from_disk",
]


async def reindex_skills_from_disk(storage: BusinessRepository, data_root: Path) -> SkillReindexReport:
    operations = SkillOperations(data_root)
    async with operations.locked():
        await operations.recover(storage)
        return await _reindex_skills_from_disk(storage, data_root)
