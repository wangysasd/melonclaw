"""远程 Skill 安装地址解析与子目录筛选。"""

from __future__ import annotations

import io
import zipfile

import pytest

from melonclaw.services.skill_import import SkillImportError, SkillImportService
from melonclaw.services.skill_remote import (
    filter_archive_subdir,
    parse_github_repo,
    resolve_remote_skill_source,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


SKILL_MD = b"---\nname: writing-guidelines\ndescription: test\n---\nbody\n"


def test_parse_shorthand() -> None:
    assert parse_github_repo("owner/repo") == ("owner", "repo", None)


def test_parse_bare_repo_url() -> None:
    assert parse_github_repo("https://github.com/owner/repo") == (
        "owner",
        "repo",
        None,
    )


def test_parse_tree_subdir_url() -> None:
    assert parse_github_repo(
        "https://github.com/vercel-labs/agent-skills/tree/main/skills/writing-guidelines"
    ) == ("vercel-labs", "agent-skills", "skills/writing-guidelines")


def test_parse_tree_branch_only_means_root() -> None:
    assert parse_github_repo(
        "https://github.com/owner/repo/tree/main"
    ) == ("owner", "repo", None)


def test_parse_strips_trailing_slash() -> None:
    assert parse_github_repo("owner/repo/") == ("owner", "repo", None)


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/owner",
        "owner/repo/tree",
        "owner/repo/tree/main/skills/../etc",
        "owner/repo/tree/main/skills//deep",
        "owner\\repo",
        "https://gitlab.com/owner/repo",
    ],
)
def test_parse_rejects_invalid(repo: str) -> None:
    with pytest.raises(SkillImportError):
        parse_github_repo(repo)


def test_resolve_source_uses_main_branch() -> None:
    source = resolve_remote_skill_source("owner/repo")
    assert source.url == "https://codeload.github.com/owner/repo/zip/main"
    assert source.subpath is None


def test_resolve_source_keeps_subpath() -> None:
    source = resolve_remote_skill_source(
        "https://github.com/owner/repo/tree/dev/skills/x"
    )
    assert source.subpath == "skills/x"
    assert source.url.endswith("/owner/repo/zip/dev")
    assert source.ref == "dev"


def test_filter_subdir_with_wrapper() -> None:
    archive = _zip_bytes(
        {
            "owner-repo-main/README.md": b"readme",
            "owner-repo-main/skills/writing-guidelines/SKILL.md": SKILL_MD,
            "owner-repo-main/skills/other/SKILL.md": b"other",
            "owner-repo-main/skills/writing-guidelines/refs/a.md": b"a",
        }
    )
    filtered = filter_archive_subdir(archive, "skills/writing-guidelines")
    assert set(_zip_entries(filtered)) == {"SKILL.md", "refs/a.md"}


def test_filter_subdir_flat_archive() -> None:
    archive = _zip_bytes(
        {
            "skills/writing-guidelines/SKILL.md": SKILL_MD,
            "README.md": b"readme",
        }
    )
    filtered = filter_archive_subdir(archive, "skills/writing-guidelines")
    assert set(_zip_entries(filtered)) == {"SKILL.md"}


def test_filter_subdir_missing() -> None:
    archive = _zip_bytes({"owner-repo-main/README.md": b"readme"})
    with pytest.raises(SkillImportError, match="找不到子目录"):
        filter_archive_subdir(archive, "skills/nope")


def test_filtered_archive_passes_import_validation(tmp_path) -> None:
    archive = _zip_bytes(
        {
            "agent-skills-main/skills/writing-guidelines/SKILL.md": SKILL_MD,
            "agent-skills-main/AGENTS.md": b"x",
        }
    )
    filtered = filter_archive_subdir(archive, "skills/writing-guidelines")
    service = SkillImportService(tmp_path)
    definition = service._extract_and_validate(filtered, tmp_path / "out")
    assert definition.id == "writing-guidelines"


def _zip_entries(archive_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return [
            entry.filename
            for entry in archive.infolist()
            if not entry.filename.endswith("/")
        ]


def test_remote_download_total_timeout_cancels_transfer(monkeypatch) -> None:
    import asyncio

    from melonclaw.services import skill_remote

    cancelled = False

    async def stalled_download(url: str) -> bytes:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True
        return b""

    assert skill_remote.DOWNLOAD_TIMEOUT_SECONDS == 120
    monkeypatch.setattr(skill_remote, "DOWNLOAD_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(skill_remote, "_download", stalled_download)
    with pytest.raises(SkillImportError, match="^安装超时$"):
        asyncio.run(skill_remote.fetch_remote_skill_archive(
            "https://codeload.github.com/owner/repo/zip/main"
        ))
    assert cancelled


def test_remote_download_socket_timeout_message(monkeypatch) -> None:
    import asyncio

    import httpx

    from melonclaw.services import skill_remote

    async def timed_out_download(url: str) -> bytes:
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(skill_remote, "_download", timed_out_download)
    with pytest.raises(SkillImportError, match="^安装超时$"):
        asyncio.run(skill_remote.fetch_remote_skill_archive(
            "https://codeload.github.com/owner/repo/zip/main"
        ))


def test_remote_source_is_pinned_before_download(monkeypatch):
    import asyncio

    import httpx

    from melonclaw.services import skill_remote

    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"sha": "a" * 40})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(skill_remote.httpx, "AsyncClient", lambda **kwargs: client)
    source = asyncio.run(skill_remote.pin_remote_source(resolve_remote_skill_source("https://github.com/o/r/tree/dev/skills/demo")))
    assert seen == ["https://api.github.com/repos/o/r/commits/dev"]
    assert source.ref == "a" * 40
    assert source.url.endswith("/zip/" + "a" * 40)
    assert source.subpath == "skills/demo"


def test_remote_subdir_filter_rejects_bomb_before_reading(monkeypatch):
    import io
    import zipfile

    from melonclaw.services import skill_remote

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repo/skills/demo/SKILL.md", b"x" * 10000)
    monkeypatch.setattr(skill_remote, "MAX_ARCHIVE_TOTAL_BYTES", 100)
    with pytest.raises(SkillImportError, match="上限"):
        filter_archive_subdir(buffer.getvalue(), "skills/demo")
