import type { ApprovalAction } from "../types/api";

/** 审批清单由服务端在安装提交时逐字段核验；仅展示，不解释或执行包内正文。 */
export function SkillInstallApproval({ action }: { action: ApprovalAction }) {
  if (action.name !== "confirm_skill_install") return null;
  let installation: Record<string, unknown>;
  try {
    const args = typeof action.args === "string" ? JSON.parse(action.args) : action.args;
    installation = args.installation;
    if (!installation || typeof installation !== "object") return null;
  } catch {
    return null;
  }
  const shared = installation.scope === "global";
  return (
    <div className="approval-description skill-install-approval" aria-label="Skill 安装清单">
      <p>技能：{String(installation.name ?? "")}</p>
      <p>来源：{String(installation.source_url || "聊天 ZIP 附件")}</p>
      <p>安装范围：{shared ? "系统共享" : "仅自己"}</p>
      <p>{installation.enable === true
        ? shared ? "安装并全员启用；所有用户均可使用。" : "安装并启用，仅自己可用。"
        : "只安装，暂不启用。"}</p>
      <p>不会执行包内脚本或安装依赖；启用后下一条消息生效。</p>
    </div>
  );
}
