"""数据库层异常定义。"""

from __future__ import annotations

class DatabaseConfigurationError(RuntimeError):
    """DATABASE_URL 缺失或驱动配置不符合当前应用要求。"""


class DatabaseUnavailableError(RuntimeError):
    """PostgreSQL 当前不可连接或无法执行基础查询。"""


class DatabaseSchemaError(RuntimeError):
    """初始化命令尚未创建应用所需的表。"""
