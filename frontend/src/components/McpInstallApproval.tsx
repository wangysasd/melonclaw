import type { ApprovalAction } from "../types/api";

const FIELD_LABELS: Record<string, string> = {
  url: "连接地址（可能含凭据）", transport: "连接类型", headers: "请求头凭据",
  env: "进程环境", command: "启动程序", args: "启动参数", enabled: "资源启用状态",
  tool_allowlist: "工具白名单", personally_enabled: "个人使用偏好",
};

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
  const updating = installation.operation === "update";
  const changes = Array.isArray(installation.changed_fields) ? installation.changed_fields.map(String) : [];
  return (
    <div className="approval-description skill-install-approval" aria-label="MCP 安装清单">
      <p>MCP：{String(installation.name ?? "")}</p>
      <p>操作：{updating ? "更新已有配置" : installation.operation === "unchanged" ? "配置相同，无需更新" : "新增安装"}</p>
      {installation.target_id ? <p>目标：{String(installation.target_id)} · 版本 v{String(installation.expected_version)}</p> : null}
      {installation.previous_connection ? <p>原连接：{String(installation.previous_connection)}</p> : null}
      <p>连接：{String(installation.connection ?? "")}（{String(installation.transport ?? "")}）</p>
      {changes.length ? <p>变更：{changes.map(field => FIELD_LABELS[field] ?? field).join("、")}</p> : null}
      {updating ? <p>未列出的配置与偏好保持原样；版本变化时不会覆盖。</p> : null}
      <p>安装范围：{installation.scope === "global" ? "系统共享（所有用户）" : "仅自己"}</p>
      <p>工具权限：{names === null ? "允许全部工具" : Array.isArray(names) && names.length ? names.join("、") : "不允许任何工具"}</p>
      {Array.isArray(installation.headers_keys) && installation.headers_keys.length ? <p>认证请求头：{installation.headers_keys.map(String).join("、")}。保留或更新后的凭据将发送到上述目标，请确认地址。</p> : null}
      {installation.shadows_global === true && <p>该个人配置会替代你看到的同名全局来源；停用后也不会自动回退。</p>}
      <p>{testing ? "测试会向目标发送请求和凭据，仅发现工具；不会保存或调用工具。"
        : `${updating ? "更新后" : "保存后"}${installation.enable === true ? installation.scope === "global" ? "全员启用，所有用户下一条消息可使用新配置。" : "启用，下一条消息生效。" : "保持停用。"}保存不代表连接测试通过。`}</p>
    </div>
  );
}
