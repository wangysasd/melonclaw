"""累计 Agent 的自定义中间件。"""

from melonclaw.memory.middleware import MemoryScopeMiddleware
from melonclaw.middleware.attachment_hydration import (
    AttachmentHydrationMiddleware,
    AttachmentHydrationProvider,
)
from melonclaw.middleware.file_ordering import FileOperationOrderingMiddleware
from melonclaw.middleware.tool_selection import CatalogToolSelectorMiddleware

__all__ = [
    "CatalogToolSelectorMiddleware",
    "FileOperationOrderingMiddleware",
    "AttachmentHydrationMiddleware",
    "AttachmentHydrationProvider",
    "MemoryScopeMiddleware",
]
