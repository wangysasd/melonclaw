"""任务定义与入队时冻结的运行配置；快照不是授权凭证。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from melonclaw.core.task_schedule import TaskSchedule


class TaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=12_000)
    model_id: str = Field(min_length=1, max_length=200)
    schedule: TaskSchedule

    @field_validator("model_id")
    @classmethod
    def concrete_model(cls, value):
        if not value.startswith("custom:") or not value.removeprefix("custom:"):
            raise ValueError("请选择数据库中明确的模型。")
        return value


def task_thread_id(run_id: UUID) -> str:
    return f"task-run:{UUID(str(run_id))}"
