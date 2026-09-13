"""文档校验：仓库内 Markdown 的相对链接必须指向真实存在的文件。

这是机械校验文档新鲜度的一部分：链接失效会让检查失败，避免文档慢慢漂移成
「看起来还在、点开已经不存在」的状态。

指向被 .gitignore 忽略的本地文件的链接会被跳过（例如 note/ 下的草稿），
因为这类文件在 CI 环境中本来就不存在。
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

SKIPPED_PREFIXES = ("http://", "https://", "mailto:", "#")

OPTIONAL_DOCS = (Path("note/note.md"),)

TRACKED_DOCS = (Path("AGENTS.md"), Path("README.md"))


def _documentation_files() -> list[Path]:
    files = [REPO_ROOT / name for name in TRACKED_DOCS]
    files.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    files.extend(REPO_ROOT / name for name in OPTIONAL_DOCS if (REPO_ROOT / name).is_file())
    return [path for path in files if path.is_file()]


def _is_git_ignored(absolute: Path) -> bool:
    """判断路径是否被 .gitignore 覆盖（对不存在的文件同样有效）。"""

    try:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", str(absolute)],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
    except OSError:  # git 不可用时不做跳过判断
        return False
    return result.returncode == 0


class DocumentationLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        broken: list[str] = []
        for document in _documentation_files():
            text = document.read_text(encoding="utf-8")
            for raw_target in LINK_PATTERN.findall(text):
                target = raw_target.split("#", 1)[0].strip()
                if not target or target.startswith(SKIPPED_PREFIXES):
                    continue
                if "<" in target or "${" in target:
                    continue
                resolved = (document.parent / target).resolve()
                if resolved.exists() or _is_git_ignored(resolved):
                    continue
                broken.append(
                    f"{document.relative_to(REPO_ROOT)} → {raw_target}"
                )
        if broken:
            self.fail(
                "以下文档链接指向不存在的文件，请修正链接或删除该条目：\n  "
                + "\n  ".join(broken)
            )

    def test_agents_map_entries_exist(self):
        """AGENTS.md 的「知识地图」表格不能指向不存在的文档。"""

        agents = REPO_ROOT / "AGENTS.md"
        self.assertTrue(agents.is_file(), "AGENTS.md 缺失")
        content = agents.read_text(encoding="utf-8")
        self.assertIn("## 知识地图", content, "AGENTS.md 缺少「知识地图」章节")
        for required in (
            "docs/ARCHITECTURE.md",
            "docs/QUALITY_SCORE.md",
            "docs/exec-plans/README.md",
            "docs/design-docs/index.md",
        ):
            self.assertIn(
                required,
                content,
                f"AGENTS.md 的知识地图应包含 {required}",
            )
            self.assertTrue(
                (REPO_ROOT / required).is_file(),
                f"AGENTS.md 指向的 {required} 不存在",
            )


if __name__ == "__main__":
    unittest.main()
