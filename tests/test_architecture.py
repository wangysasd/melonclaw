"""结构测试：强制 docs/ARCHITECTURE.md 第 3 节声明的包依赖方向。

规则按当前实测边界定义，不是理论上的完美分层。新增包、或确实需要改变依赖方向时：
先更新本文件的 FORBIDDEN_IMPORTS 与 docs/ARCHITECTURE.md 的表格，再改代码。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "melonclaw"

# 每个包禁止依赖的兄弟包。未列出的包子包（core、api）允许被任意依赖。
FORBIDDEN_IMPORTS: dict[str, frozenset[str]] = {
    "database": frozenset(
        {"repository", "services", "api", "output", "memory", "tool", "middleware", "backend"}
    ),
    "repository": frozenset(
        {"services", "api", "output", "memory", "tool", "middleware", "backend"}
    ),
    "output": frozenset(
        {"database", "repository", "services", "api", "memory", "tool", "middleware", "backend"}
    ),
    "tool": frozenset(
        {
            "database",
            "repository",
            "services",
            "api",
            "output",
            "memory",
            "middleware",
            "backend",
        }
    ),
    "memory": frozenset({"services", "api", "output", "tool", "middleware", "backend"}),
    "backend": frozenset(
        {"database", "repository", "services", "api", "output", "tool", "middleware"}
    ),
    "middleware": frozenset(
        {"database", "repository", "services", "api", "tool", "backend"}
    ),
    "services": frozenset({"api"}),
}

# 允许在任意位置被依赖的包（基础件与 Web 层）。
UNRESTRICTED_PACKAGES = frozenset({"core", "api"})

ENTRY_FILES = frozenset({"main_web.py", "main_db_init.py"})

# 品味不变式：单个源文件超过该行数应先拆分职责，而不是继续堆叠。
MAX_SOURCE_LINES = 900


def _package_name(path: Path) -> str:
    """返回文件所属的顶层包；仓库根下的入口文件返回 ``<top>``。"""

    relative = path.relative_to(PACKAGE_ROOT)
    if len(relative.parts) == 1:
        return "<top>"
    return relative.parts[0]


def _iter_source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _imported_packages(path: Path) -> list[tuple[str, int]]:
    """解析文件中的 ``melonclaw.<x>`` 导入，返回 (包名, 行号) 列表。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            continue
        for module in modules:
            parts = module.split(".")
            if len(parts) > 1 and parts[0] == "melonclaw":
                found.append((parts[1], node.lineno))
    return found


class DependencyDirectionTests(unittest.TestCase):
    def test_no_forbidden_cross_package_imports(self):
        violations: list[str] = []
        for path in _iter_source_files():
            package = _package_name(path)
            forbidden = FORBIDDEN_IMPORTS.get(package)
            if not forbidden:
                continue
            for imported, lineno in _imported_packages(path):
                if imported in forbidden:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT.parent.parent)}:{lineno} "
                        f"{package}/ 不允许依赖 {imported}/"
                    )
        if violations:
            self.fail(
                "发现反向依赖，请改为通过允许的中间层调用，"
                "或在 tests/test_architecture.py 与 docs/ARCHITECTURE.md 中同步调整规则：\n  "
                + "\n  ".join(violations)
            )

    def test_api_only_imported_by_entry_points(self):
        violations: list[str] = []
        for path in _iter_source_files():
            if _package_name(path) == "api" or path.name in ENTRY_FILES:
                continue
            for imported, lineno in _imported_packages(path):
                if imported == "api":
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT.parent.parent)}:{lineno}"
                    )
        if violations:
            self.fail(
                "api/ 只允许被入口文件导入（main_web.py / main_db_init.py）。"
                "业务代码需要复用的逻辑应下沉到 services/：\n  " + "\n  ".join(violations)
            )

    def test_rule_table_covers_all_packages(self):
        """新增包时必须显式声明规则，避免悄悄绕过依赖约束。"""

        packages = {
            _package_name(path)
            for path in _iter_source_files()
            if _package_name(path) != "<top>"
        }
        declared = set(FORBIDDEN_IMPORTS) | set(UNRESTRICTED_PACKAGES)
        missing = sorted(packages - declared)
        if missing:
            self.fail(
                "以下包未在 FORBIDDEN_IMPORTS 或 UNRESTRICTED_PACKAGES 中声明："
                + ", ".join(missing)
                + "。请在 tests/test_architecture.py 中补规则，并同步 docs/ARCHITECTURE.md。"
            )

    def test_rule_targets_exist(self):
        """规则里不能引用已经删除的包。"""

        existing = {
            path.name for path in PACKAGE_ROOT.iterdir() if path.is_dir()
        }
        unknown = sorted(
            {target for targets in FORBIDDEN_IMPORTS.values() for target in targets} - existing
        )
        self.assertEqual(unknown, [], f"规则引用了不存在的包：{unknown}")

    def test_source_file_size(self):
        oversized = []
        for path in _iter_source_files():
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > MAX_SOURCE_LINES:
                oversized.append(
                    f"{path.relative_to(PACKAGE_ROOT.parent.parent)}（{line_count} 行）"
                )
        if oversized:
            self.fail(
                f"以下源文件超过 {MAX_SOURCE_LINES} 行，请先按职责拆分"
                "（或在 tests/test_architecture.py 中显式调整上限并说明原因）：\n  "
                + "\n  ".join(oversized)
            )


if __name__ == "__main__":
    unittest.main()
