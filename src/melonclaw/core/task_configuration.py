"""聊天任务配置的部分更新；不包含身份、会话或任意任务编号。"""

from pydantic import BaseModel, ConfigDict, Field

from melonclaw.core.task_schedule import TaskSchedule


class TaskChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True,
                              hide_input_in_errors=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    instruction: str | None = Field(default=None, min_length=1, max_length=12_000,
                                    description="修改后完整的执行要求，保留未被用户修改的原要求。")
    schedule: TaskSchedule | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=200,
                                description="inspect 返回的可用模型 ID；创建时省略用管理员设置的系统默认模型，修改时省略保留原模型。")
    enabled: bool | None = None

    def patch(self):
        return self.model_dump(mode="json", exclude_none=True)
