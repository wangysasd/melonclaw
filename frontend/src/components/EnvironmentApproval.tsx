import type { ApprovalAction } from "../types/api";

export function EnvironmentApproval({ action }: { action: ApprovalAction }) {
  if (action.name !== "confirm_environment_change") return null;
  try {
    const args = typeof action.args === "string" ? JSON.parse(action.args) : action.args;
    const change = args.change as Record<string, unknown>;
    if (!change || typeof change !== "object") return null;
    return <div className="approval-description" aria-label="环境变量变更清单">
      <p>变量名：{String(change.name)}</p>
      <p>范围：{change.scope === "global" ? "系统共享（所有用户）" : "仅自己"}</p>
      <p>操作：{change.action === "create" ? "新增" : change.action === "delete" ? "删除" : "更新"}
        {change.replaces_value === true ? "，写入已提交的值" : ""}
        {typeof change.enabled === "boolean" ? change.enabled ? "，启用" : "，停用" : ""}</p>
      {typeof change.description === "string" && <p>说明：{change.description || "清空"}</p>}
      <p>{String(change.impact)}。下一轮消息生效。</p>
    </div>;
  } catch { return null; }
}
