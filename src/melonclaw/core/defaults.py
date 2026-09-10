"""平台级共享默认配置（代码默认层）。

melonclaw 的配置分三层，本模块是最底层：

1. **代码默认层**（本文件）：所有用户共享的默认 Skill 凭据变量名、默认
   模型选择等非敏感默认值，随代码演进、review 和回滚；
2. **部署者配置层**（``.env``）：凭据的值（如 ``TUSHARE_MCP_TOKEN``）、
   模型 Key、数据库连接等，由部署者在本地维护，不进仓库；
3. **用户配置层**（未来多租户）：用户自己的凭据、模型选择和上传的
   Skill，存数据库按 ``user_id`` 隔离。

铁律：本文件只允许出现变量**名**和选项**值**（如模型名），凭据的值
永远只存在于 ``.env`` 或未来的用户配置表中。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Shell 子进程环境注入
# ---------------------------------------------------------------------------

# Agent Shell 子进程默认注入的凭据变量名（值来自部署者 .env）。
# 新增仓库内 Skill 依赖新凭据时，在对应的提交里把变量名加进本集合；
# 变量名即"平台管理员替所有用户预先放行"的部署决策。
DEFAULT_AGENT_ENV_VARS: tuple[str, ...] = (
    # tushare-fetcher
    "TUSHARE_MCP_TOKEN",
    "TUSHARE_TOKEN",  # tushare-fetcher 的兼容别名
    # cicc-research-* 系列
    "APP_ID",
    "APP_SECRET",
    # mcp.json 的 ${...} 占位符同名凭据
    "YUJIAN_MCP_TOKEN",
)

# 应用自身运行凭据与连接串：无论出现在哪一层（默认集合、部署者白名单、
# 未来的用户配置），一律不注入 Agent Shell 子进程，防止被 Agent 生成
# 的脚本读取或打印。此集合与 core/config.py 的 Settings 字段保持一致，
# 只随应用自身配置演进，不随 Skill 增长。
SHELL_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "MINIMAX_API_KEY",
        "MINIMAX_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "TAVILY_API_KEY",
        "DATABASE_URL",
        "MELONCLAW_SHELL_ENV_ALLOWLIST",
    }
)

# 非敏感基础变量：Skill 脚本的文档缓存目录（~/.cache）、locale 和时区需要。
SHELL_ENV_BASE_EXACT: tuple[str, ...] = ("HOME", "LANG", "TZ")
SHELL_ENV_BASE_PREFIXES: tuple[str, ...] = ("LC_",)

# 部署者兜底白名单（.env）：逗号分隔，支持 NAME 精确匹配与 PREFIX_* 前缀
# 匹配，用于放行未纳入 DEFAULT_AGENT_ENV_VARS 的变量（仍受 DENYLIST 约束）。
SHELL_ENV_ALLOWLIST_VAR = "MELONCLAW_SHELL_ENV_ALLOWLIST"


def _parse_allowlist(value: str) -> tuple[set[str], tuple[str, ...]]:
    """解析逗号分隔的白名单，返回精确名集合与前缀元组。"""

    exact: set[str] = set()
    prefixes: list[str] = []
    for item in value.split(","):
        name = item.strip()
        if not name:
            continue
        if name.endswith("*"):
            prefix = name[:-1]
            if prefix:
                prefixes.append(prefix)
        else:
            exact.add(name)
    return exact, tuple(prefixes)


def resolve_agent_env(
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """解析 Agent Shell 子进程应获得的环境变量（PATH 除外）。

    合并顺序（后写入者优先级更高）：

    1. 基础变量：HOME / LANG / TZ / LC_*；
    2. 代码默认凭据名：``DEFAULT_AGENT_ENV_VARS`` 与进程环境的交集；
    3. 部署者白名单：``MELONCLAW_SHELL_ENV_ALLOWLIST`` 命中的变量；
    4. 用户覆盖层：``overrides``（未来多租户时传该用户的 agent_envs，
       用户值优先于部署者值）。

    最终统一剔除 ``SHELL_ENV_DENYLIST``。``overrides`` 中被剔除的键同样
    不生效——黑名单是硬边界，任何层都不能越过。
    """

    source = os.environ if environ is None else environ
    exact: set[str] = set(SHELL_ENV_BASE_EXACT)
    prefixes: list[str] = list(SHELL_ENV_BASE_PREFIXES)

    exact.update(name for name in DEFAULT_AGENT_ENV_VARS if name in source)

    allow_exact, allow_prefixes = _parse_allowlist(
        source.get(SHELL_ENV_ALLOWLIST_VAR, "")
    )
    exact.update(allow_exact)
    prefixes.extend(allow_prefixes)

    env: dict[str, str] = {}
    for name, value in source.items():
        if name in exact or any(name.startswith(prefix) for prefix in prefixes):
            env[name] = value
    if overrides:
        env.update({str(name): str(value) for name, value in overrides.items()})
    for name in SHELL_ENV_DENYLIST:
        env.pop(name, None)
    return env


# ---------------------------------------------------------------------------
# 模型默认选择
# ---------------------------------------------------------------------------

# load_settings() 的兜底默认：.env 未指定默认模型键时使用。
# DEEPAGENTS_PROVIDER 的推荐值是具体模型配置键，而不是供应商名称。
DEFAULT_PROVIDER = "DEEPSEEK_MODEL_FLASH"
DEFAULT_MODEL = "deepseek-chat"

# 系统模型目录使用的稳定 ID。配置值仍然来自 .env，这些 ID 只表示
# 平台维护的模型槽位，不表示用户可以任意传入 provider 或模型名。
DEEPSEEK_FLASH_MODEL_ID = "system:deepseek:flash"
DEEPSEEK_PRO_MODEL_ID = "system:deepseek:pro"
MINIMAX_M3_MODEL_ID = "system:minimax:m3"
MINIMAX_M27_MODEL_ID = "system:minimax:m27"
OPENAI_MODEL_ID = "system:openai:default"
