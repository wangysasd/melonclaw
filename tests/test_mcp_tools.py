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

        self.assertEqual([tool.name for tool in tools], ["healthy_tool"])
        self.assertEqual(catalog, {"healthy": ("healthy_tool",)})
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

        self.assertEqual([tool.name for tool in tools], ["healthy_tool"])

    async def test_allowlist_failure_is_isolated_to_one_server(self):
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

        self.assertEqual([tool.name for tool in tools], ["healthy_tool"])
        self.assertEqual(catalog, {"healthy": ("healthy_tool",)})
        self.assertIn("白名单工具不存在", failures["filtered"])

    async def test_all_mcp_failures_still_leave_agent_tools_available(self):
        from melonclaw.tool.tools import build_agent_tools

        class Client:
            def __init__(self, _servers):
                pass

            async def get_tools(self, *, server_name):
                raise ConnectionError(f"{server_name} unavailable")

        settings = SimpleNamespace(
            mcp_servers={
                "first": {"transport": "http", "url": "https://first.test/mcp"},
                "second": {"transport": "http", "url": "https://second.test/mcp"},
            },
            mcp_tool_allowlists={},
        )
        with patch("melonclaw.tool.tools.MultiServerMCPClient", Client):
            tools = await build_agent_tools(settings)

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
            "request failed: https://secret.test/mcp?token=<redacted>",
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
