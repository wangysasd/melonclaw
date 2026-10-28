"""远程 Skill 市场安装：GitHub zipball 纯 HTTP 下载（零代码执行）。

支持 GitHub 和公网 HTTPS 下载接口：下载 zip 后走与 ZIP 上传完全相同的
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
import ipaddress
import re
import socket
import zipfile
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

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

    if repo.startswith("https://") and urlparse(repo).hostname != "github.com":
        validate_download_url(repo)
        return RemoteSkillSource(url=repo, subpath=None, source_url=repo, ref="")
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


def expand_skill_archives(archive_bytes: bytes) -> list[tuple[str, bytes]]:
    """展开 skills/ 下的嵌套 ZIP 合集；只读取结构，不执行外层安装说明。

    共用展开字节/条目预算，最多两层、64 个技能；单技能仍走完整导入校验。
    """
    from melonclaw.services.skill_import import SkillImportService

    result = []
    budget = [0, 0]

    def visit(payload, label, depth):
        if len(payload) > MAX_ARCHIVE_TOTAL_BYTES:
            raise SkillImportError("技能合集超过大小上限。")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                total = sum(entry.file_size for entry in entries)
                budget[0] += total
                budget[1] += len(entries)
                if (budget[0] > MAX_ARCHIVE_TOTAL_BYTES or budget[1] > MAX_ARCHIVE_ENTRIES
                        or total / max(len(payload), 1) > MAX_ARCHIVE_COMPRESSION_RATIO
                        or any(entry.file_size > MAX_SKILL_FILE_SIZE for entry in entries)):
                    raise SkillImportError("技能合集展开后超过大小、文件数量或压缩比上限。")
                paths = [(entry, SkillImportService._safe_relative_path(entry.filename))
                         for entry in entries if entry.filename and not entry.is_dir()]
                nested = [(entry, path) for entry, path in paths
                          if "skills" in path.parts[:-1] and path.suffix.lower() == ".zip"]
                if nested:
                    if depth >= 2:
                        raise SkillImportError("技能合集嵌套层数超过上限。")
                    for entry, path in nested:
                        visit(archive.read(entry), f"{label}/{path.as_posix()}".lstrip("/"), depth + 1)
                else:
                    result.append((label, payload))
                    if len(result) > 64:
                        raise SkillImportError("技能合集最多包含 64 个技能。")
        except zipfile.BadZipFile as exc:
            raise SkillImportError("下载的技能合集包含无效 ZIP。") from exc

    visit(archive_bytes, "", 0)
    return result


async def fetch_remote_skill_archive(url: str) -> bytes:
    """经公网校验与连接 IP 固定下载远程 ZIP，限制大小与总超时。"""

    validate_download_url(url)
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            return await _download(url)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise SkillImportError("安装超时") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise SkillImportError("下载远程 Skill 失败，请检查网络后重试。") from exc


def _sanitize_subpath(subpath: str) -> str:
    parts = subpath.replace("\\", "/").split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SkillImportError(f"子目录路径不合法：{subpath!r}")
    return "/".join(parts)


def validate_download_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        valid = (len(url) <= 4096 and parsed.scheme == "https" and parsed.hostname
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment
                 and not parsed.hostname.lower().rstrip(".").endswith((".local", ".localhost", ".internal"))
                 and parsed.hostname.lower().rstrip(".") != "localhost"
                 and "%" not in parsed.hostname
                 and not any(ord(c) <= 32 for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise SkillImportError("下载地址必须是无凭据的公网 HTTPS 地址（443 端口）。")


class PublicDownloadTransport(httpx.AsyncHTTPTransport):
    """DNS 校验后以固定 IP 建立连接，TLS/SNI 仍校验原始主机，防 DNS 重绑定。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_download_url(str(request.url))
        host = request.url.host
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, 443, type=socket.SOCK_STREAM,
        )
        ips = [address[4][0] for address in addresses]
        def public_ip(value):
            ip = ipaddress.ip_address(value)
            return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)

        if not ips or any(not public_ip(ip) for ip in ips):
            raise SkillImportError("拒绝从本地、内网或保留地址下载 Skill。")
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=ips[0]),
            headers={**dict(request.headers), "host": request.headers["host"]}, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await super().handle_async_request(pinned)


async def _download(url: str) -> bytes:
    # 不读取服务进程代理/认证环境，不转发 Cookie 或凭据。
    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT_SECONDS, trust_env=False,
        transport=PublicDownloadTransport(limits=httpx.Limits(max_keepalive_connections=0)),
    ) as client:
        for _ in range(6):
            validate_download_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SkillImportError("下载重定向缺少目标地址。")
                    url = urljoin(url, location)
                    client.cookies.clear()
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise SkillImportError("远程 Skill 包超过大小上限。")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise SkillImportError("下载重定向次数超过上限。")


__all__ = [
    "RemoteSkillSource",
    "fetch_remote_skill_archive",
    "filter_archive_subdir",
    "parse_github_repo",
    "resolve_remote_skill_source",
]
