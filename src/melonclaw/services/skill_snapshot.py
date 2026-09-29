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
