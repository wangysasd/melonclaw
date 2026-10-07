"""显式部署操作入口，必须在服务停止时运行。"""

from __future__ import annotations

import argparse
from pathlib import Path

from melonclaw.core.config import load_settings
from melonclaw.storage.deployment import copy_persistent_tree, install_builtin_skills


def main() -> None:
    parser = argparse.ArgumentParser(description="安装内置模板或复制持久目录，不删除原数据。")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install-builtins")
    install.add_argument("--source", type=Path, required=True, help="仓库 .data/skills/shared")
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--service-stopped", action="store_true", required=True,
                        help="确认已停止所有应用进程和写入")
    args = parser.parse_args()
    try:
        if args.command == "install-builtins":
            count = install_builtin_skills(args.source.expanduser(), load_settings().data_root)
            print(f"已安装 {count} 个缺失的内置 Skill；已有 Skill 未覆盖。")
        else:
            count = copy_persistent_tree(args.source.expanduser(), args.destination.expanduser())
            print(f"已复制 {count} 个缺失文件并校验；源目录未删除。")
    except (ValueError, OSError):
        # 不回显底层异常，避免路径、远程地址等敏感信息进入部署日志。
        parser.exit(1, "部署操作失败：检查目录、权限、符号链接或同名内容冲突；源数据保留。\n")


if __name__ == "__main__":
    main()
