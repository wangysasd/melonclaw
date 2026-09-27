"""远程 Skill 市场安装：GitHub zipball 纯 HTTP 下载（零代码执行）。

这是唯一默认开放的远程安装能力：下载 zip 后走与 ZIP 上传完全相同的
SkillImportService 校验与两段式确认。更灵活的 skills.sh CLI 安装需要
执行不可信代码，默认关闭（见 docs/design-docs/user-skills-and-mcp.md）。
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse
from urllib.request import urlopen

from melonclaw.services.skill_import import (
    MAX_ARCHIVE_TOTAL_BYTES,
    SkillImportError,
)

GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {"codeload.github.com", "github.com", "raw.githubusercontent.com"}
)
DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_DOWNLOAD_BYTES = MAX_ARCHIVE_TOTAL_BYTES


def github_zipball_url(repo: str) -> str:
    """把 ``owner/repo`` 简写解析为 codeload zipball URL。"""

    normalized = repo.strip().removeprefix("https://github.com/").strip("/")
    if not GITHUB_REPO_RE.fullmatch(normalized):
        raise SkillImportError(
            "仓库格式不正确，请使用 owner/repo 或 https://github.com/owner/repo。"
        )
    owner, name = normalized.split("/", 1)
    return f"https://codeload.github.com/{owner}/{name}/zip/refs/heads/main"


async def fetch_remote_skill_archive(url: str) -> bytes:
    """按 host 白名单下载远程 zip，限制大小与超时。"""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOWNLOAD_HOSTS:
        raise SkillImportError(f"不允许从该地址下载 Skill：{parsed.netloc or url!r}")
    return await asyncio.to_thread(_download, url)


def _download(url: str) -> bytes:
    try:
        with urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SkillImportError("远程 Skill 包超过大小上限。")
                chunks.append(chunk)
    except SkillImportError:
        raise
    except Exception as exc:
        raise SkillImportError(f"下载远程 Skill 失败：{exc}") from exc
    return b"".join(chunks)


__all__ = ["fetch_remote_skill_archive", "github_zipball_url"]
