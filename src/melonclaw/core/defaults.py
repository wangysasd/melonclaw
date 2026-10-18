"""Shell 基础环境与禁止注入规则；业务值只来自数据库资源。"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

SHELL_ENV_BASE_EXACT = ("HOME", "LANG", "TZ")
SHELL_ENV_BASE_PREFIXES = ("LC_",)
SHELL_ENV_DENYLIST = frozenset({
    "PATH", "DATABASE_URL", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "GLOBIGNORE",
})
SHELL_ENV_DENY_PREFIXES = ("MELONCLAW_", "LANGFUSE_", "LANGSMITH_", "LD_", "DYLD_", "PYTHON", "PG")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def managed_env_name(name: str) -> bool:
    return bool(ENV_NAME_RE.fullmatch(name)) and not (
        name in SHELL_ENV_BASE_EXACT or name in SHELL_ENV_DENYLIST
        or name.startswith((*SHELL_ENV_BASE_PREFIXES, *SHELL_ENV_DENY_PREFIXES))
    )


def resolve_agent_env(environ: Mapping[str, str] | None = None,
                      overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    env = {name: value for name, value in source.items()
           if name in SHELL_ENV_BASE_EXACT or name.startswith(SHELL_ENV_BASE_PREFIXES)}
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("TZ", "UTC")
    env.update({name: value for name, value in (overrides or {}).items() if managed_env_name(name)})
    return env
