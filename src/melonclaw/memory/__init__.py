"""MelonClaw Memory 能力域的统一入口。"""

from melonclaw.memory.middleware import MemoryScopeMiddleware
from melonclaw.memory.service import (
    DEFAULT_AGENT_ID,
    DEFAULT_INSTALLATION_ID,
    MemoryAuthorizationError,
    MemoryConflictError,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemoryValidationError,
    namespace_for_context,
)
from melonclaw.memory.tools import build_memory_tools

__all__ = [
    "DEFAULT_AGENT_ID",
    "DEFAULT_INSTALLATION_ID",
    "MemoryAuthorizationError",
    "MemoryConflictError",
    "MemoryRecord",
    "MemoryScope",
    "MemoryScopeMiddleware",
    "MemoryService",
    "MemoryValidationError",
    "build_memory_tools",
    "namespace_for_context",
]

