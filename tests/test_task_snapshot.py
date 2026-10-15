"""任务定义不携带启用状态、项目、Skill 或可重放的权限。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_task_agent import resolved_model

from melonclaw.core.task_snapshot import TaskDefinition, task_thread_id
from melonclaw.services.tasks import TaskService


def definition():
    return TaskDefinition(
        name="研究",
        instruction="整理公开资料",
        model_id="custom:offline",
        schedule={"kind": "manual"},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "  "},
        {"instruction": "  "},
        {"name": "a" * 121},
        {"instruction": "a" * 12001},
        {"model_id": ""},
        {"model_id": "default"},
        {"model_id": "custom:"},
        {"tenant_id": "injected"},
        {"owner_user_id": "victim"},
        {"project_id": str(uuid4())},
        {"skill_id": "skill"},
        {"enabled": True},
    ],
)
def test_definition_rejects_invalid_and_removed_fields(overrides):
    with pytest.raises(ValidationError):
        TaskDefinition(**{**definition().model_dump(), **overrides})


def test_history_snapshot_only_contains_display_information():
    snapshot = TaskService.snapshot({"version": 1}, definition(), resolved_model())
    assert set(snapshot) == {"definition", "task_version", "model_display_name"}
    assert "offline-placeholder" not in str(snapshot)
    assert snapshot["definition"] == definition().model_dump(mode="json")
    first, second = task_thread_id(uuid4()), task_thread_id(uuid4())
    assert first != second and first.startswith("task-run:")
