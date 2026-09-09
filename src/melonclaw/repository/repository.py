"""业务持久化仓储门面。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from melonclaw.database.database import Database
from melonclaw.repository.conversations import ConversationRepositoryMixin
from melonclaw.repository.locks import ConcurrencyMixin
from melonclaw.repository.memory_events import MemoryEventRepositoryMixin
from melonclaw.repository.projects import ProjectRepositoryMixin
from melonclaw.repository.users import UserRepositoryMixin


class BusinessRepository(
    UserRepositoryMixin,
    ProjectRepositoryMixin,
    ConversationRepositoryMixin,
    ConcurrencyMixin,
    MemoryEventRepositoryMixin,
):
    """使用 Database 提供的 Engine 访问业务持久化数据。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    def engine(self) -> AsyncEngine:
        return self.database.engine
