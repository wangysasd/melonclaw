import type { DisplayEvent } from "../types/api";
import type { AgentRun } from "../lib/agentRun";
import { stoppedRunSummary } from "../lib/runActivity";

export function StoppedRunNotice({ run, events, onSync, syncDisabled = false }: { run: AgentRun; events: DisplayEvent[]; onSync: () => void; syncDisabled?: boolean }) {
  const summary = stoppedRunSummary(run, events);
  return <div className="stopped-run-notice" role="status">
    <p>已停止生成。{summary.preserved.length
      ? `已保留：${summary.preserved.join("、")}。` : "尚未收到可保留的回复或工具结果。"}</p>
    {summary.completed.length ? <details>
      <summary>已收到完成结果的工具调用（{summary.completed.length} 项）</summary>
      <ul>{summary.completed.map((label, index) => <li key={index}>{label}</li>)}</ul>
    </details> : null}
    {summary.failed.length ? <details>
      <summary>已报告失败的工具调用（{summary.failed.length} 项）</summary>
      <ul>{summary.failed.map((label, index) => <li key={index}>{label}</li>)}</ul>
    </details> : null}
    {summary.unconfirmed.length ? <details open>
      <summary>结果待确认的工具调用（{summary.unconfirmed.length} 项）</summary>
      <ul>{summary.unconfirmed.map((label, index) => <li key={index}>{label}</li>)}</ul>
    </details> : null}
    {summary.runningAgents > 0 ? <p>{summary.runningAgents} 个子 Agent 未收到结束确认。</p> : null}
    <p>停止生成不会撤销已执行的操作。未收到结果也不代表操作未执行。</p>
    {summary.unconfirmed.length || summary.runningAgents > 0
      ? <button type="button" onClick={onSync} disabled={syncDisabled}>重新同步会话，核对结果</button> : null}
  </div>;
}
