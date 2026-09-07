"""通过 STDIO 运行 FastMCP 数学 Server。"""

from __future__ import annotations

from fastmcp import FastMCP


mcp = FastMCP(
    "MCP Study Math STDIO",
    instructions="用于学习 MCP 工具发现、调用和 STDIO 传输的最小数学 Server。",
)


@mcp.tool()
def add(a: int, b: int) -> int:
    """返回两个整数之和。"""

    return a + b


@mcp.tool()
def multiply(a: int, b: int) -> int:
    """返回两个整数之积。"""

    return a * b


@mcp.tool()
def explain_transport(transport: str) -> str:
    """解释本次学习中使用的 MCP 传输方式。"""

    descriptions = {
        "stdio": "STDIO：Client 启动本地 Server 子进程，通过 stdin/stdout 传输 MCP 消息。",
        "sse": "SSE：Client 通过 HTTP /sse 连接远程 Server；该方式主要用于兼容 SSE 示例。",
    }
    return descriptions.get(transport.lower(), "未知传输方式；可选值为 stdio 或 sse。")


def main() -> None:
    """启动由 Client 管理生命周期的 STDIO Server。"""

    # STDIO 是协议通道，Server 不应向 stdout 写入普通日志。
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
