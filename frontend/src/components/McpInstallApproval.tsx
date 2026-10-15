import type { ApprovalAction } from "../types/api";

export function McpInstallApproval({ action }: { action: ApprovalAction }) {
  if (!["test_mcp_install", "confirm_mcp_install"].includes(action.name)) return null;
  let installation: Record<string, unknown>;
  try {
    const args = typeof action.args === "string" ? JSON.parse(action.args) : action.args;
    installation = args.installation;
    if (!installation || typeof installation !== "object") return null;
  } catch { return null; }
  const names = installation.tool_allowlist;
  const testing = action.name === "test_mcp_install";
  return (
    <div className="approval-description skill-install-approval" aria-label="MCP 安装清单">
      <p>MCP：{String(installation.name ?? "")}</p>
      <p>连接：{String(installation.connection ?? "")}（{String(installation.transport ?? "")}）</p>
      <p>安装范围：{installation.scope === "global" ? "系统共享（所有用户）" : "仅自己"}</p>
      <p>工具权限：{names === null ? "允许全部工具，每次调用仍需审批" : Array.isArray(names) && names.length ? names.join("、") : "不允许任何工具"}</p>
      {installation.shadows_global === true && <p>该个人配置会替代你看到的同名全局来源；停用后也不会自动回退。</p>}
      <p>{testing ? "测试会向目标发送请求和已提供凭据，仅发现工具；不会安装或调用工具。"
        : installation.enable === true ? installation.scope === "global" ? "安装并全员启用，所有用户下一条消息可用。" : "安装并启用，下一条消息生效。" : "只安装，暂不启用。"}</p>
    </div>
  );
}
