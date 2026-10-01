import { describe, expect, it } from "vitest";
import { importMcp, mcpCandidates, parseMcpJson } from "../src/lib/mcpImport";

describe("MCP single-direction JSON import", () => {
  it("extracts named configurations and normalizes transports", () => {
    const choices = mcpCandidates(JSON.stringify({ mcpServers: { demo: { type: "streamable_http", url: "https://example.com/mcp", headers: { Authorization: "Bearer test-secret" } } } }));
    expect(importMcp(choices[0], false)).toMatchObject({ name: "demo", transport: "http", headers: { Authorization: "Bearer test-secret" } });
  });
  it("requires an explicit URL transport and allows multi-server selection", () => {
    const choices = mcpCandidates('{"mcpServers":{"a":{"url":"https://a.test/sse"},"b":{"command":"uvx"}}}');
    expect(choices).toHaveLength(2);
    expect(importMcp(choices[0], false).transport).toBe("");
    expect(importMcp(choices[1], true).transport).toBe("stdio");
    expect(() => importMcp(choices[1], false)).toThrow("stdio");
  });
  it.each([
    '{"url":"a","url":"b"}',
    '{"headers":{"Authorization":"a","Authorization":"b"}}',
    '{"url":"a",}',
    '{"__proto__":{}}',
    '[]',
    '{"url":"unterminated}',
  ])("rejects invalid or ambiguous JSON without echoing input", text => {
    expect(() => parseMcpJson(text)).toThrow();
  });
  it("rejects unknown fields, forged scope and process configuration for users", () => {
    for (const config of [{ scope: "global" }, { type: "stdio", command: "npx" }, { type: "http", command: "npx" }, { type: "http", url: "${SECRET}" }, { type: "http", headers: { A: "1", a: "2" } }]) {
      expect(() => importMcp({ name: "", config }, false)).toThrow();
    }
  });
  it("bounds parsing size and depth", () => {
    expect(() => parseMcpJson(' '.repeat(65537))).toThrow("64 KiB");
    expect(() => parseMcpJson('{"a":'.repeat(18) + '0' + '}'.repeat(18))).toThrow("16 层");
  });
});
