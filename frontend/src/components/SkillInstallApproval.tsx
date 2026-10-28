import type { ApprovalAction } from "../types/api";

/** 只展示服务端清单；提交时逐字段核验，不执行包内说明。 */
export function SkillInstallApproval({ action }: { action: ApprovalAction }) {
  if (!["confirm_skill_install", "confirm_skill_batch"].includes(action.name)) return null;
  let installations: Record<string, unknown>[];
  try {
    const args = typeof action.args === "string" ? JSON.parse(action.args) : action.args;
    installations = action.name === "confirm_skill_batch" ? args.installations : [args.installation];
    if (!Array.isArray(installations) || !installations.length || installations.some(item => !item || typeof item !== "object")) return null;
  } catch {
    return null;
  }
  return (
    <div className="approval-description skill-install-approval" aria-label="Skill 安装清单">
      {installations.length > 1 ? <p>共 {installations.length} 项技能，各项独立提交；失败项可单独重试。</p> : null}
      {installations.map((installation, index) => {
        const shared = installation.scope === "global";
        const updating = installation.operation === "update";
        return <div key={String(installation.draft_id ?? index)}>
          <p>技能：{String(installation.name ?? "")}</p>
          <p>操作：{updating ? "更新已有技能，保留启用状态" : installation.operation === "unchanged" ? "内容相同，无需更新" : "新增安装"}</p>
          <p>来源：{String(installation.source_url ||
            (String(installation.source_ref ?? "").startsWith("chat:") ? "聊天生成" : "聊天 ZIP 附件"))}</p>
          <p>安装范围：{shared ? "系统共享" : "仅自己"}</p>
          <p>{updating
            ? installation.enable === true ? shared ? "保持全员启用。" : "保持个人启用。" : "保持停用。"
            : installation.enable === true
              ? shared ? "安装并全员启用；所有用户均可使用。" : "安装并启用，仅自己可用。"
              : "只安装，暂不启用。"}</p>
        </div>;
      })}
      <p>不会执行包内脚本或安装依赖；启用后下一条消息生效。</p>
    </div>
  );
}
