import type { ChatMessage } from "../hooks/useChatStream";
import type { AgentRun } from "../lib/agentRun";
import { stoppedRunSummary } from "../lib/runActivity";

export function FailureNotice({ message, run, onSync, syncDisabled = false }: { message: ChatMessage; run: AgentRun; onSync: () => void; syncDisabled?: boolean }) {
  const summary = stoppedRunSummary(run, message.events);
  const failed = message.status === "failed";
  if (!failed && message.errorCode !== "network_disconnected" && !summary.failed.length) return null;
  const network = message.errorCode === "network_disconnected";
  const model = message.errorCode === "model_execution_failed";
  return <div className="stopped-run-notice" role="status">
    <p>{network ? "网络连接中断，执行结果尚未确认。" : model ? "模型调用失败，请检查模型配置或更换可用模型。" : summary.failed.length || message.errorCode === "tool_execution_failed" ? "部分工具执行失败，请检查下方工具结果与输入条件。" : "助手执行失败，请先核对会话状态。"}</p>
    {failed ? <p>本次回复未完成，当前显示已接收的内容。</p> : null}
    {summary.failed.length ? <details open><summary>失败的工具（{summary.failed.length} 项）</summary><ul>{summary.failed.map((label, index) => <li key={index}>{label}</li>)}</ul></details> : null}
    {summary.completed.length ? <details><summary>已完成的工具（{summary.completed.length} 项）</summary><ul>{summary.completed.map((label, index) => <li key={index}>{label}</li>)}</ul></details> : null}
    {summary.unconfirmed.length ? <details open><summary>结果待确认的工具（{summary.unconfirmed.length} 项）</summary><ul>{summary.unconfirmed.map((label, index) => <li key={index}>{label}</li>)}</ul></details> : null}
    <p>失败或断线不会撤销已执行的操作。请核对结果后，在新消息中说明还需要完成的部分；系统不会重跑整轮。</p>
    <button type="button" onClick={onSync} disabled={syncDisabled}>重新同步会话，核对结果</button>
  </div>;
}
