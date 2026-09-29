"""管理页、选择器和执行入口共用的 Skill 有效状态。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from melonclaw.services.skill_content import content_manifest, parse_requirements
from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skills import SkillCatalog, SkillDefinition, read_skill


@dataclass(frozen=True)
class SkillState:
    row: dict[str, Any]
    definition: SkillDefinition | None
    availability: str
    diagnostic: str
    personally_enabled: bool
    shadowed: bool = False

    @property
    def unavailable_reason(self) -> str | None:
        if self.availability != "ready":
            return self.availability
        if not self.row["enabled"] and self.row["scope"] == "global":
            return "globally_disabled"
        if not self.personally_enabled:
            return "personally_disabled"
        if self.shadowed:
            return "shadowed"
        return None

    @property
    def effective_enabled(self) -> bool:
        return self.unavailable_reason is None

    def public_dict(self) -> dict[str, Any]:
        row, definition = self.row, self.definition
        return {
            "id": str(row["id"]),
            "selection_id": f"{row['scope']}:{row['name']}",
            "name": row["name"],
            "scope": row["scope"],
            "source_type": row["source_type"],
            "source_url": row["source_url"],
            "source_ref": row["source_ref"],
            "version": row["version"],
            "content_hash": definition.content_hash if definition else row["content_hash"],
            "enabled": row["enabled"],
            "user_enabled": row["user_enabled"],
            "personally_enabled": self.personally_enabled,
            "effective_enabled": self.effective_enabled,
            "unavailable_reason": self.unavailable_reason,
            "created_by": row["created_by"],
            "display_name": definition.display_name if definition else row["name"],
            "description": definition.description if definition else "",
            "availability": self.availability,
            "diagnostic": self.diagnostic,
            "shadowed": self.shadowed,
        }


def evaluate_skills(
    rows: list[dict[str, Any]], catalog: SkillCatalog, root: Path
) -> list[SkillState]:
    definitions = {(item.scope, item.id): item for item in catalog.list()}
    states = []
    for row in rows:
        definition = definitions.get((row["scope"], row["name"]))
        directory = root / row["storage_path"]
        availability, diagnostic = "ready", ""
        try:
            directory = SkillOperations(root.parent).target(row["storage_path"])
            expected = f"shared/{row['name']}" if row["scope"] == "global" else f"users/{row['created_by']}/{row['name']}"
            if row["storage_path"] != expected:
                raise ValueError("技能目录与索引归属不一致。")
            if not directory.exists():
                availability, diagnostic = "missing", "目录已丢失，可上传更新修复或删除记录。"
            elif definition is None:
                read_skill(directory)
                raise ValueError("技能目录与索引范围不一致。")
            else:
                _, metadata = read_skill(directory)
                parse_requirements(metadata)
                digest, _ = content_manifest(directory)
                definition = replace(
                    definition,
                    resource_id=str(row["id"]),
                    version=row["version"],
                    content_hash=digest,
                )
        except (ValueError, OSError) as exc:
            availability = "invalid"
            diagnostic = str(exc) if isinstance(exc, ValueError) else "技能文件无法读取。"
            definition = None
        if row["status"] != "ready":
            availability, diagnostic = "pending", "内容操作尚未完成，请管理员执行恢复。"
        personal = (
            row["user_enabled"] is not False if row["scope"] == "global" else bool(row["enabled"])
        )
        states.append(SkillState(row, definition, availability, diagnostic, personal))
    own_names = {
        state.row["name"]
        for state in states
        if state.row["scope"] == "user" and state.effective_enabled
    }
    return [
        replace(state, shadowed=state.row["scope"] == "global" and state.row["name"] in own_names)
        for state in states
    ]
