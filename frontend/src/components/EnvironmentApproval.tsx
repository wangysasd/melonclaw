import type { ApprovalAction } from "../types/api";

export function EnvironmentApproval({ action }: { action: ApprovalAction }) {
  if (action.name !== "confirm_environment_change") return null;
  try {
    const args = typeof action.args === "string" ? JSON.parse(action.args) : action.args;
    const change = args.change as Record<string, unknown>;
    if (!change || typeof change !== "object") return null;
    const changes = (Array.isArray(change.changes) ? change.changes : [change]) as Record<string, unknown>[];
    if (!changes.length || changes.some(item => !item || typeof item !== "object")) return null;
    return <div className="approval-description" aria-label="环境变量变更清单">
      {changes.length > 1 ? <p>共 {changes.length} 项变量，同一事务保存；任一项失败则全部不提交。</p> : null}
      {changes.map((item, index) => <div key={String(item.name ?? index)}>
        <p>变量名：{String(item.name)}</p>
        <p>范围：{item.scope === "global" ? "系统共享（所有用户）" : "仅自己"}</p>
        <p>操作：{item.action === "create" ? "新增" : item.action === "delete" ? "删除" : "更新"}
          {item.replaces_value === true ? "，写入已提交的值" : ""}
          {typeof item.enabled === "boolean" ? item.enabled ? "，启用" : "，停用" : ""}</p>
        {typeof item.description === "string" && <p>说明：{item.description || "清空"}</p>}
        <p>{String(item.impact)}。下一轮消息生效。</p>
      </div>)}
    </div>;
  } catch { return null; }
}
