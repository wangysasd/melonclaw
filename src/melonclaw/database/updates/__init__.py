"""按序执行的、保留已有数据的数据库升级步骤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.updates.account_management import upgrade as account_management


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
)
