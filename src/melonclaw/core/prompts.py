"""累计学习 Agent 的系统提示词。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime


BASE_SYSTEM_PROMPT = "你是一个强大的助手，默认用中文回答问题。"

VIRTUAL_FILE_WORKSPACE_GUIDANCE = """
文件工具操作的是独立的虚拟运行时工作区。只能使用虚拟 POSIX 路径，例如
`/summary.md`、`/notes/outline.md`；绝不能使用宿主机绝对路径、`~`、当前
进程的真实目录或用户主目录。用户要求写入 `summary.md` 时，直接使用
`write_file(file_path="/summary.md", ...)`，无需先枚举宿主目录。仅当任务确实
需要运行命令时才使用 Shell；不得用 Shell 发现或访问宿主机路径。

工具调用会并发执行。存在先后依赖时不得放在同一批调用中：写入新文件后，先
等待 `write_file` 的成功 ToolMessage；只有下一轮才可以 `read_file` 验证内容。
""".strip()

INTERPRETER_GUIDANCE = """
需要在内存中循环、分支、并行搜索或确定性整理结构化数据时，优先使用
`eval` 的 JavaScript Interpreter；简单的一两个工具调用仍直接调用工具。
Interpreter 中只有 `tools.internetSearch(...)` 是显式开放的外部能力，文件、
Shell 和 MCP 工具不能从 Interpreter 内调用。需要命令执行、安装依赖或访问
文件系统时使用普通 `execute` / 文件工具，并遵守其人工审批流程。
""".strip()

TUSHARE_MCP_GUIDANCE = """
你可以使用 Tushare MCP 查询金融数据。遇到沪深股票基础信息、交易日历、
行情或复权因子问题时，优先依据工具名称、描述和参数 schema 选择合适工具。
调用前从用户问题中提取代码、交易所和日期范围；缺少关键参数时先询问，
不要猜测。回答时只陈述工具实际返回的数据，并明确日期与数据口径；
空结果、权限不足或接口错误必须如实说明。
""".strip()

def build_system_prompt(
    mcp_server_names: Collection[str],
    *,
    current_date: date | None = None,
) -> str:
    """加入本地日期，并按已启用的 MCP 服务补充领域调用指引。"""

    today = current_date or datetime.now().astimezone().date()
    sections = [
        BASE_SYSTEM_PROMPT,
        VIRTUAL_FILE_WORKSPACE_GUIDANCE,
        INTERPRETER_GUIDANCE,
        f"今天的日期是 {today.isoformat()}（以应用启动时的本地时区为准）。",
    ]
    if "tushare_mcp" in mcp_server_names:
        sections.append(TUSHARE_MCP_GUIDANCE)
    return "\n\n".join(sections)


def build_tool_selection_prompt(
    tools: Collection[tuple[str, str]],
    max_tools: int,
) -> str:
    """为兼容 DeepSeek 的普通 JSON 工具选择调用生成提示词。"""

    catalog = "\n".join(
        f"- {name}: {description}"
        for name, description in tools
    )
    return f"""
你是工具路由器。根据用户最新问题，从下面目录选择完成任务最相关的工具。

要求：
1. 最多选择 {max_tools} 个工具，按相关性从高到低排列。
2. 只能使用目录中原样出现的工具名，不要创造名称。
3. 如果不需要任何目录工具，返回空数组。
4. 只返回合法 JSON，不要解释，不要使用 Markdown。

返回格式：{{"tools": ["tool_name_1", "tool_name_2"]}}

工具目录：
{catalog}
""".strip()
