import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { mcpChatDisplay } from "../src/lib/mcpChat";
import { McpInstallApproval } from "../src/components/McpInstallApproval";

describe("chat MCP credential isolation", () => {
  const raw = JSON.stringify({ mcpServers: { demo: { url: "https://example.test/token-secret", headers: { Authorization: "Bearer private-key" } } } });
  it.each([raw, `帮我安装\n\`\`\`json\n${raw}\n\`\`\``, `安装 ${raw}`, `安装\n\`\`\`json\n${raw}\n\`\`\`\n以及 ${raw}`])("does not put raw configuration in optimistic history or sidebar", content => {
    const display = mcpChatDisplay(content);
    expect(display).not.toContain("private-key");
    expect(display).not.toContain("token-secret");
    expect(display).toContain("MCP 配置已提交");
  });
  it("keeps ordinary messages intact", () => {
    expect(mcpChatDisplay("解释 MCP" )).toBe("解释 MCP");
    expect(mcpChatDisplay("```json\n{\"count\":2}\n```" )).toContain('"count"');
  });
  it("shows personal scope, empty whitelist and global shadowing on approval", () => {
    render(<McpInstallApproval action={{ name: "confirm_mcp_install", allowed_decisions: ["approve", "reject"], description: "",
      args: JSON.stringify({ installation: { name: "demo", connection: "https://example.test/…", transport: "http", enable: true, tool_allowlist: [], shadows_global: true } }) }} />);
    const card = screen.getByLabelText("MCP 安装清单");
    expect(card.textContent).toContain("仅自己");
    expect(card.textContent).toContain("不允许任何工具");
    expect(card.textContent).toContain("不会自动回退");
    expect(card.textContent).toContain("下一条消息生效");
  });
});
