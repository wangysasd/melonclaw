"""环境配置工具只接收不含值的引用与清单，不进入子 Agent 或 PTC。"""

from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict


class EnvironmentChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    name: str
    scope: Literal["global", "user"]
    action: Literal["create", "update", "delete"]
    expected_version: int | None
    replaces_value: bool
    enabled: bool | None
    description: str | None
    impact: str


class SingleEnvironmentChange(EnvironmentChange):
    draft_id: str


class EnvironmentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    draft_id: str
    scope: Literal["global", "user"]
    changes: list[EnvironmentChange]


def build_environment_tools(provider):
    @tool
    async def inspect_environment(*, runtime: ToolRuntime[Any]) -> dict:
        """查看当前身份可维护的环境变量名称、说明和状态；不读取任何值。"""
        return await provider.inspect(runtime.context)

    @tool
    async def prepare_environment_change(draft_id: str, *, runtime: ToolRuntime[Any]) -> dict:
        """用户明确要求维护环境变量时，按当前消息的 draft_id 准备单项或批量的不含值审批清单。

        只能使用服务端已提取的草稿；管理员创建共享变量，普通用户创建个人变量。
        不接收 value，不读取服务器环境或已有值。下一步原样调用 confirm_environment_change。
        """
        return await provider.prepare(runtime.context, draft_id)

    @tool
    async def confirm_environment_change(
        change: SingleEnvironmentChange | EnvironmentBatch, *, runtime: ToolRuntime[Any]
    ) -> dict:
        """原样提交 prepare 返回的 change，经人工审批后维护环境变量，下一轮生效。

        拒绝后停止，不得通过 Shell、HTTP、SQL 或文件绕过；不编造或修改范围及版本。
        """
        return await provider.confirm(runtime.context, change.model_dump())

    return [inspect_environment, prepare_environment_change, confirm_environment_change]
