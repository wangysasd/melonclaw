"""后台每次执行的当前有效资源；不复制 Skill 内容或缓存 Agent。"""

from dataclasses import dataclass, field
from pathlib import Path

from melonclaw.core.task_policy import TaskExecutionError
from melonclaw.services.mcp import resolve_user_mcp_servers
from melonclaw.services.skill_operations import SkillOperations
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import (
    GLOBAL_SKILLS_ROUTE,
    USER_SKILLS_ROUTE,
    SkillCatalog,
    SkillRoot,
)


@dataclass(frozen=True)
class TaskResources:
    skill_mounts: tuple[tuple[str, Path], ...]
    mcp_servers: dict = field(repr=False)
    mcp_tool_allowlists: dict = field(repr=False)


async def load_task_resources(storage, settings, user_id: str) -> TaskResources:
    """身份由执行入口校验；可见性和有效状态继续使用资源管理的唯一规则。"""
    root = settings.data_root / "skills"
    catalog = SkillCatalog((
        SkillRoot("global", GLOBAL_SKILLS_ROUTE, root / "shared"),
        SkillRoot("user", USER_SKILLS_ROUTE, root / "users" / user_id),
    ))
    operations = SkillOperations(settings.data_root)
    for _ in range(3):
        generation = await storage.resource_versions.read(user_id)
        async with operations.locked():
            rows = await storage.list_visible_skill_rows(user_id, include_disabled=True)
            states = evaluate_skills(rows, catalog, root)
            mounts = tuple(
                (state.definition.virtual_path.removesuffix("SKILL.md"),
                 operations.target(state.row["storage_path"]))
                for state in states
                if state.effective_enabled and state.definition is not None
            )
        servers, allowlists = resolve_user_mcp_servers(await storage.list_visible_mcp_rows(user_id))
        if generation == await storage.resource_versions.read(user_id):
            return TaskResources(mounts, servers, allowlists)
    raise TaskExecutionError("resources_changed", "资源配置持续变化，请稍后重新运行。")
