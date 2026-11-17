"""按序执行的、保留已有数据的数据库升级步骤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.updates.account_management import upgrade as account_management
from melonclaw.database.updates.environment import upgrade as environment
from melonclaw.database.updates.knowledge import upgrade as knowledge
from melonclaw.database.updates.model_purpose import upgrade as model_purpose
from melonclaw.database.updates.parser_model import upgrade as parser_model
from melonclaw.database.updates.task_center import upgrade as task_center
from melonclaw.database.updates.task_reliability import upgrade as task_reliability


@dataclass(frozen=True)
class SchemaUpdate:
    update_id: str
    description: str
    upgrade: Callable[[AsyncConnection], Awaitable[None]]


UPDATES = (
    SchemaUpdate(
        update_id="20261006_account_management",
        description="账户管理与 Cookie 登录",
        upgrade=account_management,
    ),
    SchemaUpdate("20261010_task_center", "任务中心三表与固定会话", task_center),
    SchemaUpdate("20261015_task_reliability", "移除任务通知与操作回执保留策略", task_reliability),
    SchemaUpdate("20261017_environment", "环境变量资源与聊天审批操作", environment),
    SchemaUpdate("20261117_knowledge", "知识库八张表与检索视图", knowledge),
    SchemaUpdate("20261117_model_purpose", "已有模型补上用途并调整默认索引", model_purpose),
    SchemaUpdate("20261117_parser_model", "知识库补上与本地一致的解析模型列", parser_model),
)
