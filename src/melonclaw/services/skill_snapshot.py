"""为一次 Agent 构建固定有效 Skill 内容；派生快照不参与索引重建。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import GLOBAL_SKILLS_ROUTE, USER_SKILLS_ROUTE


def clear_stale_snapshots(data_root: Path) -> int:
    """启动时清空 .snapshots/。运行期快照只被内存中的 Agent 缓存引用，
    进程退出后即成死数据；启动阶段尚无请求和缓存，直接全量删除（含 .tmp-* 半成品）。
    删除失败静默跳过，不阻塞启动。"""

    snapshot_root = data_root / "skills" / ".snapshots"
    if not snapshot_root.is_dir():
        return 0
    removed = 0
    for child in snapshot_root.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


async def skill_snapshot(storage, user_id: str, data_root: Path, catalog):
    operations = SkillOperations(data_root)
    async with operations.locked():
        rows = await storage.list_visible_skill_rows(user_id, include_disabled=True)
        states = evaluate_skills(rows, catalog, data_root / "skills")
        active = [
            state for state in states if state.effective_enabled and state.definition is not None
        ]
        references = [state.definition.reference() for state in active]
        revision = hashlib.sha256(json.dumps(references, sort_keys=True).encode()).hexdigest()
        destination = data_root / "skills" / ".snapshots" / revision
        if not destination.exists():
            temporary = destination.with_name(f".tmp-{uuid4().hex}")
            temporary.mkdir(parents=True)
            try:
                for state in active:
                    target = temporary / state.row["scope"] / state.row["name"]
                    shutil.copytree(operations.target(state.row["storage_path"]), target)
                temporary.rename(destination)
            except BaseException:
                shutil.rmtree(temporary)
                raise
        directories = tuple(
            (route, destination / scope)
            for scope, route in (("global", GLOBAL_SKILLS_ROUTE), ("user", USER_SKILLS_ROUTE))
            if (destination / scope).is_dir()
        )
        return revision, directories, references
