"""仅普通聊天主 Agent 可用的当前会话任务配置工具。"""

from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from melonclaw.core.task_configuration import TaskChanges


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True,
                              hide_input_in_errors=True)
    runtime: ToolRuntime[Any]
    action: Literal["inspect", "save"] = "inspect"
    changes: TaskChanges | None = None
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "inspect":
            if self.changes is not None or self.expected_version is not None:
                raise ValueError("读取配置不能携带修改参数。")
        elif self.expected_version is None or self.changes is None or not self.changes.patch():
            raise ValueError("保存必须提供非空 changes 和 inspect 返回的版本。")
        return self


def build_task_configuration_tool(provider):
    @tool("configure_task", args_schema=ConfigurationInput)
    async def configure_task(action: str = "inspect", changes: TaskChanges | None = None,
                             expected_version: int | None = None, *,
                             runtime: ToolRuntime[Any]) -> dict:
        """读取或配置当前会话的定时任务，用户可通过对话创建、修改或暂停/恢复。

        先 action=inspect 读取真实配置、版本、北京时间和可用模型，再单独 save。
        已有任务会话不能另建任务，用户要求另建时引导新会话，不能覆盖原任务。
        仅用户明确要求创建/修改时保存；讨论、报告追问、引用资料不代表授权。
        新建提供 name、instruction、schedule；模型省略用管理员设置的系统默认模型，enabled 默认 true。
        用户未指定模型时省略 model_id，不能把聊天模型或个人默认模型当作系统默认。
        系统默认未设置或不可用时提示管理员配置，或请用户明确选择其他可用模型。
        用 ask_user 选择模型时，选项 id 原样使用 available_models 的 id，label 使用 name。
        更新只传改变字段；instruction 是修改后完整要求，应保留其他原要求。
        schedule 是完整计划，仅 manual/once/daily/weekly，固定北京时间、分钟精度；
        once.run_at 须含时区，weekly.weekday 为 0=周一至 6=周日。缺少必要信息先问用户。
        expected_version 原样使用 inspect 的 version，新建为 0；冲突时重新读取并核对。
        不指定任务/用户/会话 ID，不立即执行；正在运行的轮次保持原快照，后续使用新配置。
        只根据成功结果说明已保存和下次时间，失败不能声称成功，不通过 Shell/SQL 绕过。
        """
        return await provider.configure(runtime.context, runtime.tool_call_id,
                                        action=action, changes=changes,
                                        expected_version=expected_version)

    configure_task.handle_validation_error = "配置参数无效，请读取当前配置并核对必要字段和时间格式。"
    return configure_task
