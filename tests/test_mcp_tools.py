import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from melonclaw.tool.tools import (
    _load_mcp_tools_with_catalog,
    build_mcp_catalog_tool,
    load_mcp_tools,
)


class FakeTool:
    def __init__(self, name: str):
        self.name = name
        self.description = "测试工具"
        self.args_schema = {"type": "object", "properties": {}}


class FakeResponse:
    status_code = 401


class HttpError(Exception):
    response = FakeResponse()


class McpToolLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_server_does_not_block_successful_server(self):
        servers = {
            "healthy": {"transport": "http", "url": "https://healthy.test/mcp"},
            "unauthorized": {
                "transport": "http",
                "url": "https://unauthorized.test/mcp",
            },
        }

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                if server_name == "unauthorized":
                    raise ExceptionGroup("discovery failed", [HttpError()])
                return [FakeTool("healthy_tool")]

        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools, catalog, failures = await _load_mcp_tools_with_catalog(servers)

        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0].name.startswith("mcp__healthy__healthy_tool__"))
        self.assertEqual(catalog, {"healthy": (tools[0].name,)})
        self.assertEqual(failures, {"unauthorized": "HTTP 401"})

    async def test_public_loader_returns_successes_when_one_server_fails(self):
        servers = {
            "healthy": {"transport": "http", "url": "https://healthy.test/mcp"},
            "broken": {"transport": "http", "url": "https://broken.test/mcp"},
        }

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                if server_name == "broken":
                    raise ConnectionError("server unavailable")
                return [FakeTool("healthy_tool")]

        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools = await load_mcp_tools(servers)

        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0].name.startswith("mcp__healthy__healthy_tool__"))

    async def test_missing_allowlist_tools_do_not_expand_permissions(self):
        servers = {
            "healthy": {"transport": "http", "url": "https://healthy.test/mcp"},
            "filtered": {"transport": "http", "url": "https://filtered.test/mcp"},
        }

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                return [FakeTool(f"{server_name}_tool")]

        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools, catalog, failures = await _load_mcp_tools_with_catalog(
                servers,
                {"filtered": ("missing_tool",)},
            )

        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0].name.startswith("mcp__healthy__healthy_tool__"))
        self.assertEqual(catalog, {"healthy": (tools[0].name,), "filtered": ()})
        self.assertEqual(failures, {})

    async def test_all_mcp_failures_still_leave_agent_tools_available(self):
        from melonclaw.tool.tools import build_agent_tools

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                raise ConnectionError(f"{server_name} unavailable")

        settings = SimpleNamespace(
        )
        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools = await build_agent_tools(
                settings,
                mcp_servers={
                    "first": {"transport": "http", "url": "https://first.test/mcp"},
                    "second": {"transport": "http", "url": "https://second.test/mcp"},
                },
                mcp_tool_allowlists={},
            )

        catalog = next(
            tool for tool in tools if getattr(tool, "name", "") == "list_mcp_tools"
        )
        result = catalog.invoke({})
        self.assertEqual(result["tool_count"], 0)
        self.assertEqual(result["failed_server_count"], 2)

    async def test_failure_summary_redacts_token_in_exception_text(self):
        servers = {
            "secret": {"transport": "http", "url": "https://secret.test/mcp"},
        }

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                raise RuntimeError(
                    "request failed: https://secret.test/mcp?token=secret-value"
                )

        with patch.dict(os.environ, {"TUSHARE_MCP_TOKEN": "secret-value"}), patch(
            "melonclaw.tool.tools.MultiServerMCPClient", Client
        ):
            _, _, failures = await _load_mcp_tools_with_catalog(servers)

        self.assertEqual(
            failures["secret"],
            "MCP 连接或协议错误",
        )

    def test_catalog_exposes_failed_server_without_credentials(self):
        tool = build_mcp_catalog_tool(
            {
                "healthy": {"transport": "http"},
                "broken": {"transport": "http"},
            },
            {"healthy": ("read_data",)},
            {"broken": "HTTP 401"},
        )

        result = tool.invoke({})

        self.assertEqual(result["failed_server_count"], 1)
        self.assertEqual(result["tool_count"], 1)
        self.assertEqual(
            result["servers"],
            [
                {
                    "name": "broken",
                    "transport": "http",
                    "tools": [],
                    "status": "failed",
                    "error": "HTTP 401",
                },
                {
                    "name": "healthy",
                    "transport": "http",
                    "tools": ["read_data"],
                    "status": "ready",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()


def test_connection_identity_changes_tool_name_and_requires_approval():
    from langchain_core.tools import StructuredTool

    from melonclaw.core.hitl import mcp_interrupts
    from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
    from melonclaw.tool.tools import wrap_mcp_tool

    async def lookup(query: str) -> str:
        """查找数据。"""
        return query

    original = StructuredTool.from_function(coroutine=lookup)
    first = wrap_mcp_tool("demo", original, {"transport": "http", "url": "https://first.test"})
    changed = wrap_mcp_tool("demo", original, {"transport": "http", "url": "https://second.test"})
    assert first.name != changed.name
    assert first.name in mcp_interrupts([first])
    assert first.name not in INTERPRETER_PTC_TOOLS


def test_tool_result_and_error_do_not_expose_connection_credentials():
    import asyncio

    from langchain_core.tools import StructuredTool

    from melonclaw.tool.tools import wrap_mcp_tool

    async def echo() -> str:
        """模拟服务不小心回显凭据。"""
        return "Bearer secret-test / secret-test"

    original = StructuredTool.from_function(coroutine=echo)
    wrapped = wrap_mcp_tool("demo", original, {"headers": {"Authorization": "Bearer secret-test"}})
    assert "secret-test" not in asyncio.run(wrapped.ainvoke({}))


def test_partial_allowlist_keeps_existing_tools_and_ignores_new_tools():
    import asyncio

    class Client:
        def __init__(self, _servers):
            pass

        async def get_tools(self, **kwargs):
            return [FakeTool("search"), FakeTool("write")]

    async def run():
        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools, catalog, failures = await _load_mcp_tools_with_catalog(
                {"demo": {"transport": "http", "url": "https://example.com/mcp"}},
                {"demo": ("search", "removed")},
            )
        assert len(tools) == 1 and "__search__" in tools[0].name
        assert catalog == {"demo": (tools[0].name,)} and not failures

    asyncio.run(run())
