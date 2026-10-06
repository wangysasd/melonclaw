"""业务持久化仓储门面。"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from melonclaw.database.database import Database
from melonclaw.repository.accounts import AccountRepositoryMixin
from melonclaw.repository.artifacts import ArtifactRepositoryMixin
from melonclaw.repository.attachments import AttachmentRepositoryMixin
from melonclaw.repository.conversations import ConversationRepositoryMixin
from melonclaw.repository.locks import ConcurrencyMixin
from melonclaw.repository.mcp import McpRepositoryMixin
from melonclaw.repository.mcp_install import McpInstallRepositoryMixin
from melonclaw.repository.memory_events import MemoryEventRepositoryMixin
from melonclaw.repository.projects import ProjectRepositoryMixin
from melonclaw.repository.resources import ResourceRepositoryMixin
from melonclaw.repository.user_interactions import UserInteractionRepositoryMixin
from melonclaw.repository.users import UserRepositoryMixin


class BusinessRepository(
    AccountRepositoryMixin,
    UserRepositoryMixin,
    ProjectRepositoryMixin,
    ConversationRepositoryMixin,
    ArtifactRepositoryMixin,
    AttachmentRepositoryMixin,
    UserInteractionRepositoryMixin,
    ResourceRepositoryMixin,
    McpRepositoryMixin,
    McpInstallRepositoryMixin,
    ConcurrencyMixin,
    MemoryEventRepositoryMixin,
):
    """使用 Database 提供的 Engine 访问业务持久化数据。"""

    def __init__(self, database: Database) -> None:
        self.database = database
        # 留出业务查询连接，避免所有连接被等待下游 SQL 的身份锁占满。
        self._account_slots = asyncio.Semaphore(4)

    @property
    def engine(self) -> AsyncEngine:
        return self.database.engine
