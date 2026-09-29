"""远程 Skill 市场安装：GitHub zipball 纯 HTTP 下载（零代码执行）。

这是唯一默认开放的远程安装能力：下载 zip 后走与 ZIP 上传完全相同的
SkillImportService 校验与两段式确认。更灵活的 skills.sh CLI 安装需要
执行不可信代码，默认关闭（见 docs/design-docs/user-skills-and-mcp.md）。

约定的安装地址（纯仓库默认 main，tree 链接尊重分支并固定 commit）：
- ``owner/repo`` 或 ``https://github.com/owner/repo``：仓库根目录本身是单个 Skill；
- ``https://github.com/owner/repo/tree/<分支>/<子目录>``：从仓库子目录中
  定位单个 Skill（Skill 集合仓库的常见形态）。
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from dataclasses import dataclass, replace
from urllib.parse import urlparse

import httpx

from melonclaw.services.skill_import import (
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_SKILL_FILE_SIZE,
    SkillImportError,
)

GITHUB_TREE_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/tree/[^/]+(?:/(.+))?)?$"
)
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {"codeload.github.com", "github.com", "raw.githubusercontent.com"}
)
DOWNLOAD_TIMEOUT_SECONDS = 120
MAX_DOWNLOAD_BYTES = MAX_ARCHIVE_TOTAL_BYTES


@dataclass(frozen=True)
class RemoteSkillSource:
    """解析后的远程 Skill 来源：整仓 zipball 地址 + 可选的子目录路径。"""

    url: str
    subpath: str | None
    source_url: str
    ref: str


def parse_github_repo(repo: str) -> tuple[str, str, str | None]:
    """把用户输入解析为 ``(owner, name, subpath)``。

    支持 ``owner/repo``、纯仓库 URL 以及 ``/tree/<分支>/<子目录>`` 链接；
    分支由 resolve_remote_skill_source 单独解析，下载前固定 commit。
    """

    normalized = repo.strip().removeprefix("https://github.com/").strip("/")
    match = GITHUB_TREE_RE.fullmatch(normalized)
    if match is None:
        raise SkillImportError(
            "仓库格式不正确，请使用 owner/repo 或 https://github.com/owner/repo，"
            "集合仓库可使用 https://github.com/owner/repo/tree/<分支>/<子目录>。"
        )
    owner, name, subpath = match.groups()
    if owner in {".", ".."} or name in {".", ".."}:
        raise SkillImportError("仓库名称不合法。")
    if subpath is not None:
        subpath = _sanitize_subpath(subpath)
    return owner, name, subpath


def resolve_remote_skill_source(repo: str) -> RemoteSkillSource:
    """解析安装地址为 codeload zipball URL + 可选子目录。"""

    owner, name, subpath = parse_github_repo(repo)
    normalized = repo.strip().removeprefix("https://github.com/").strip("/")
    parts = normalized.split("/")
    ref = parts[3] if len(parts) > 3 else "main"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", ref) or ref in {".", ".."}:
        raise SkillImportError("远程分支或 commit 不合法。")
    url = f"https://codeload.github.com/{owner}/{name}/zip/{ref}"
    return RemoteSkillSource(url=url, subpath=subpath, source_url=f"https://github.com/{normalized}", ref=ref)


async def pin_remote_source(source: RemoteSkillSource) -> RemoteSkillSource:
    """将显式分支/tag 固定到 commit 后再下载，预览来源与安装字节一致。"""
    parts = urlparse(source.source_url).path.strip("/").split("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"https://api.github.com/repos/{parts[0]}/{parts[1]}/commits/{source.ref}")
            response.raise_for_status()
            commit = response.json()["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid commit")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SkillImportError("无法解析 GitHub commit，请检查地址或稍后重试（可能达到访问限额）。") from exc
    return replace(source, url=f"https://codeload.github.com/{parts[0]}/{parts[1]}/zip/{commit}", ref=commit)


def filter_archive_subdir(archive_bytes: bytes, subpath: str) -> bytes:
    """从整仓 zip 中筛出子目录内容并重写为相对路径。

    codeload zip 通常套一层 ``owner-repo-main`` 顶层目录；筛完后 entry 变为
    子目录内的相对路径（拍平形态），交给 SkillImportService 常规校验。
    """

    if not archive_bytes:
        raise SkillImportError("下载的 ZIP 为空。")
    try:
        source = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillImportError("下载的文件不是合法 ZIP。") from exc

    entries = source.infolist()
    total = sum(entry.file_size for entry in entries)
    if (len(entries) > MAX_ARCHIVE_ENTRIES or total > MAX_ARCHIVE_TOTAL_BYTES
            or total / len(archive_bytes) > MAX_ARCHIVE_COMPRESSION_RATIO
            or any(entry.file_size > MAX_SKILL_FILE_SIZE for entry in entries)):
        source.close()
        raise SkillImportError("远程压缩包超出文件数量、大小或压缩比上限。")
    names = [
        entry.filename.replace("\\", "/")
        for entry in source.infolist()
        if entry.filename and not entry.filename.endswith("/")
    ]
    top_levels = {name.split("/")[0] for name in names}
    wrapper = top_levels.pop() if len(top_levels) == 1 else None
    prefix = f"{wrapper}/{subpath}/" if wrapper is not None else f"{subpath}/"

    buffer = io.BytesIO()
    matched = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            if not entry.filename or entry.filename.endswith("/"):
                continue
            normalized = entry.filename.replace("\\", "/")
            if not normalized.startswith(prefix):
                continue
            relative = normalized[len(prefix):]
            if not relative or any(
                part in ("", ".", "..") for part in relative.split("/")
            ):
                continue
            with source.open(entry) as fileobj:
                payload = fileobj.read()
            target.writestr(relative, payload)
            matched += 1
    if matched == 0:
        raise SkillImportError(f"仓库指定版本中找不到子目录 {subpath!r}。")
    source.close()
    return buffer.getvalue()


async def fetch_remote_skill_archive(url: str) -> bytes:
    """按 host 白名单下载远程 zip，限制大小与超时。"""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOWNLOAD_HOSTS:
        raise SkillImportError(f"不允许从该地址下载 Skill：{parsed.netloc or url!r}")
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            return await _download(url)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise SkillImportError("安装超时") from exc
    except httpx.HTTPError as exc:
        raise SkillImportError("下载远程 Skill 失败，请检查网络后重试。") from exc


def _sanitize_subpath(subpath: str) -> str:
    parts = subpath.replace("\\", "/").split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SkillImportError(f"子目录路径不合法：{subpath!r}")
    return "/".join(parts)


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(64 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SkillImportError("远程 Skill 包超过大小上限。")
                chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "RemoteSkillSource",
    "fetch_remote_skill_archive",
    "filter_archive_subdir",
    "parse_github_repo",
    "resolve_remote_skill_source",
]
