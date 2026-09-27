"""MCP 配置装配与安全边界校验。"""

import unittest

from melonclaw.core.mcp_config import row_to_client_config
from melonclaw.services.mcp import (
    McpConfigError,
    resolve_user_mcp_servers,
    validate_mcp_payload,
)


def make_row(**overrides):
    row = {
        "slug": "svc",
        "scope": "user",
        "source_type": "manual",
        "transport": "http",
        "url": "https://example.com/mcp",
        "command": None,
        "args": None,
        "env": {},
        "headers": {},
        "tool_allowlist": None,
    }
    row.update(overrides)
    return row


class ValidateMcpPayloadTests(unittest.TestCase):
    def test_user_scope_rejects_stdio(self):
        with self.assertRaises(McpConfigError):
            validate_mcp_payload(
                scope="user",
                transport="stdio",
                url=None,
                command="npx",
                env=None,
                headers=None,
            )

    def test_global_scope_allows_stdio(self):
        validate_mcp_payload(
            scope="global",
            transport="stdio",
            url=None,
            command="npx",
            env={"KEY": "value"},
            headers=None,
        )

    def test_user_scope_rejects_env_placeholder(self):
        with self.assertRaises(McpConfigError):
            validate_mcp_payload(
                scope="user",
                transport="http",
                url="https://example.com/mcp",
                command=None,
                env={"API_KEY": "${SECRET_KEY}"},
                headers=None,
            )

    def test_user_scope_rejects_placeholder_in_url(self):
        with self.assertRaises(McpConfigError):
            validate_mcp_payload(
                scope="user",
                transport="sse",
                url="https://example.com/?token=${SECRET}",
                command=None,
                env=None,
                headers=None,
            )

    def test_http_requires_url(self):
        with self.assertRaises(McpConfigError):
            validate_mcp_payload(
                scope="global",
                transport="http",
                url=None,
                command=None,
                env=None,
                headers=None,
            )


class ResolveUserMcpServersTests(unittest.TestCase):
    def test_row_to_client_config_http(self):
        config = row_to_client_config(make_row(env={"A": "1"}, headers={"H": "2"}))
        self.assertEqual(
            config,
            {
                "transport": "http",
                "url": "https://example.com/mcp",
                "env": {"A": "1"},
                "headers": {"H": "2"},
            },
        )

    def test_row_to_client_config_stdio(self):
        config = row_to_client_config(
            make_row(
                transport="stdio",
                url=None,
                command="npx",
                args=["-y", "some-mcp"],
                env={"A": "1"},
            )
        )
        self.assertEqual(
            config,
            {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "some-mcp"],
                "env": {"A": "1"},
            },
        )

    def test_user_scope_expands_with_empty_environ(self):
        with self.assertRaises(RuntimeError):
            resolve_user_mcp_servers(
                [make_row(url="https://example.com/?t=${APP_SECRET}")]
            )

    def test_global_scope_expands_from_process_environ(self):
        import os

        os.environ["MELONCLAW_TEST_TOKEN"] = "tok123"
        try:
            servers, _ = resolve_user_mcp_servers(
                [
                    make_row(
                        scope="global",
                        url="https://example.com/?t=${MELONCLAW_TEST_TOKEN}",
                    )
                ]
            )
        finally:
            del os.environ["MELONCLAW_TEST_TOKEN"]
        self.assertEqual(servers["svc"]["url"], "https://example.com/?t=tok123")

    def test_allowlist_passed_through(self):
        _, allowlists = resolve_user_mcp_servers(
            [make_row(tool_allowlist=["tool_a", "tool_b"])]
        )
        self.assertEqual(allowlists["svc"], ("tool_a", "tool_b"))


if __name__ == "__main__":
    unittest.main()
