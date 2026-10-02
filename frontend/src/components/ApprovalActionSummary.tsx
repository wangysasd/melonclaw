import { TextDiff } from "./TextDiff";
import type { ApprovalAction } from "../types/api";

export function approvalArgs(action: ApprovalAction): Record<string, unknown> | null {
  try {
    const args: unknown = JSON.parse(action.args);
    return args && typeof args === "object" && !Array.isArray(args)
      ? args as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

/** 只有能明确展示授权对象的操作才默认收起完整参数。 */
export function hasApprovalSummary(action: ApprovalAction): boolean {
  const args = approvalArgs(action);
  if (!args) return false;
  if (["confirm_skill_install", "test_mcp_install", "confirm_mcp_install"].includes(action.name)) {
    return Boolean(args.installation && typeof args.installation === "object");
  }
  if (action.name === "execute") return typeof args.command === "string";
  return ["write_file", "edit_file", "delete"].includes(action.name)
    && typeof args.file_path === "string";
}

/** 只展示服务端已脱敏的参数，不执行内容，也不猜测文件是否存在。 */
export function ApprovalActionSummary({ action }: { action: ApprovalAction }) {
  const args = approvalArgs(action);
  if (!args) return null;
  if (action.name === "execute" && typeof args.command === "string") {
    return <div className="approval-operation-summary">
      <span className="approval-detail-label">命令</span>
      <pre className="approval-command">{args.command}</pre>
      <p>工作目录：{typeof args.cwd === "string" ? args.cwd : "当前会话工作区"}</p>
      <p>允许后将执行此命令，可能修改文件或访问外部服务。</p>
    </div>;
  }
  if (!["write_file", "edit_file", "delete"].includes(action.name) || typeof args.file_path !== "string") return null;
  return <div className="approval-operation-summary">
    <span className="approval-detail-label">目标文件</span>
    <p className="approval-path">{args.file_path}</p>
    <p className={action.name === "delete" ? "approval-impact" : undefined}>
      {action.name === "delete" ? "将删除此文件或目录，拒绝后不会执行本次删除。"
        : action.name === "edit_file" ? "将替换指定文本，请检查修改前后的内容。"
          : "将写入以下内容，具体创建或覆盖行为由工具执行结果确认。"}
    </p>
    {action.name === "write_file" && typeof args.content === "string" ? <details className="approval-preview">
      <summary>查看写入内容</summary><pre className="approval-args">{args.content}</pre>
    </details> : null}
    {action.name === "edit_file" ? <details className="approval-preview">
      <summary>查看修改前后</summary>
      {args.replace_all === true ? <p className="approval-impact">替换全部匹配项</p> : <p>替换首个匹配项；存在歧义时由工具拒绝。</p>}
      {typeof args.old_string === "string" && typeof args.new_string === "string" ? <TextDiff before={args.old_string} after={args.new_string} fileName={args.file_path as string} /> : <p>请查看完整参数</p>}
    </details> : null}
  </div>;
}
