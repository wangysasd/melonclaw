"""通过 SSE 运行 FastMCP 数学 Server。"""

from __future__ import annotations

import argparse

from fastmcp import FastMCP


mcp = FastMCP(
    "MCP Study Math SSE",
    instructions="用于学习 MCP 工具发现、调用和 SSE 传输的最小数学 Server。",
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


def build_parser() -> argparse.ArgumentParser:
    """构造 SSE Server 的命令行参数。"""

    parser = argparse.ArgumentParser(description="运行 MCP Study 数学 SSE Server")
    parser.add_argument("--host", default="127.0.0.1", help="SSE 监听地址。")
    parser.add_argument("--port", type=int, default=8000, help="SSE 监听端口。")
    return parser


def main() -> None:
    """启动 SSE Server。"""

    args = build_parser().parse_args()
    mcp.run(
        transport="sse",
        host=args.host,
        port=args.port,
        show_banner=True,
    )


if __name__ == "__main__":
    main()
