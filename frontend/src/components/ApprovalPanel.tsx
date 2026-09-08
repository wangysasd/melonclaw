import { useMemo, useState } from "react";

import { Icon } from "./Icon";
import { toolSummary } from "./ToolTimeline";
import type {
  ApprovalAction,
  ApprovalDecision,
  ApprovalInterrupt,
  DecisionType,
  PendingApproval,
} from "../types/api";

/**
 * HITL 审批面板：展示待审批工具调用并提交用户决定。
 *
 * - 单 interrupt 时请求为顶层 actions，多 interrupt 时为 interrupts 数组，此处统一归一化。
 * - 每个操作一行：决定类型下拉（approve/edit/reject/respond）、参数折叠详情、
 *   编辑参数（JSON 校验，错误贴近输入框）与拒绝原因输入。
 * - 提交时按 interrupt 分组：单 interrupt 平铺 decisions 数组，
 *   多 interrupt 发送 { interrupt_id, decisions } 分组，保持服务端恢复协议。
 * - 编辑时固定原工具名，不能借编辑替换为未审工具。
 */

const DECISION_LABELS: Record<DecisionType, string> = {
  approve: "批准",
  edit: "编辑参数",
  reject: "拒绝",
  respond: "返回结果",
};

function normalizeInterrupts(approval: PendingApproval): ApprovalInterrupt[] {
  if (Array.isArray(approval.interrupts) && approval.interrupts.length > 0) {
    return approval.interrupts;
  }
  return [{ id: approval.id || "", actions: approval.actions || [] }];
}

function formatArgs(action: ApprovalAction): string {
  const value: Record<string, unknown> | undefined = action.args;
  if (!value) return "{}";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface RowConfig {
  interruptId: string;
  action: ApprovalAction;
}

interface RowState {
  choice: DecisionType;
  editedArgs: string;
  rejectMessage: string;
  error: string;
}

export type ApprovalDecisions =
  | ApprovalDecision[]
  | { interrupt_id: string; decisions: ApprovalDecision[] }[];

interface ApprovalPanelProps {
  approval: PendingApproval;
  onSubmit: (decisions: ApprovalDecisions) => Promise<void>;
}

export function ApprovalPanel({ approval, onSubmit }: ApprovalPanelProps) {
  const interrupts = useMemo(() => normalizeInterrupts(approval), [approval]);
  const rows = useMemo<RowConfig[]>(
    () =>
      interrupts.flatMap((interrupt) =>
        (interrupt.actions || []).map((action) => ({
          interruptId: interrupt.id || "",
          action,
        })),
      ),
    [interrupts],
  );

  const [rowStates, setRowStates] = useState<RowState[]>(() =>
    rows.map((row) => ({
      choice: (row.action.allowed_decisions || ["approve", "edit", "reject"])[0],
      editedArgs: formatArgs(row.action),
      rejectMessage: "",
      error: "",
    })),
  );
  const [submitting, setSubmitting] = useState(false);
  const [argsOpen, setArgsOpen] = useState<boolean[]>(() =>
    rows.map(() => false),
  );

  const updateRow = (index: number, patch: Partial<RowState>) => {
    setRowStates((previous) =>
      previous.map((state, i) => (i === index ? { ...state, ...patch } : state)),
    );
  };

  const handleSubmit = async () => {
    // 先本地校验，全部通过后再组装提交。
    const decisionsByInterrupt = new Map<string, ApprovalDecision[]>(
      interrupts.map((interrupt) => [interrupt.id || "", []]),
    );
    const nextStates = rowStates.map((state) => ({ ...state, error: "" }));
    let hasError = false;

    rows.forEach((row, index) => {
      const state = rowStates[index];
      let decision: ApprovalDecision;
      if (state.choice === "approve") {
        decision = { type: "approve" };
      } else if (state.choice === "reject" || state.choice === "respond") {
        decision = { type: state.choice, message: state.rejectMessage };
      } else {
        let args: unknown;
        try {
          args = JSON.parse(state.editedArgs);
        } catch (error) {
          nextStates[index].error = `第 ${index + 1} 项参数不是合法 JSON：${
            error instanceof Error ? error.message : String(error)
          }`;
          hasError = true;
          return;
        }
        if (!args || typeof args !== "object" || Array.isArray(args)) {
          nextStates[index].error = `第 ${index + 1} 项参数必须是 JSON 对象。`;
          hasError = true;
          return;
        }
        decision = {
          type: "edit",
          edited_action: {
            name: row.action.name || "unknown",
            args: args as Record<string, unknown>,
          },
        };
      }
      const bucket = decisionsByInterrupt.get(row.interruptId);
      if (!bucket) {
        nextStates[index].error = "审批请求已变化，请刷新会话后重试。";
        hasError = true;
        return;
      }
      bucket.push(decision);
    });

    if (hasError) {
      setRowStates(nextStates);
      return;
    }

    const decisions: ApprovalDecisions =
      interrupts.length === 1
        ? (decisionsByInterrupt.get(interrupts[0].id || "") ?? [])
        : interrupts.map((interrupt) => ({
            interrupt_id: interrupt.id || "",
            decisions: decisionsByInterrupt.get(interrupt.id || "") || [],
          }));

    setSubmitting(true);
    try {
      await onSubmit(decisions);
    } finally {
      setSubmitting(false);
    }
  };

  const hasRows = rows.length > 0;

  return (
    <div className="approval-panel">
      <div className="approval-title">
        <Icon name="shield-check" size={19} />
        需要你确认一项操作
      </div>
      <div className="approval-copy">
        助手提出了需要确认的操作。你可以批准、编辑参数或拒绝；编辑时不能替换工具名称。
      </div>
      <div className="approval-actions">
        {rows.map((row, index) => {
          const state = rowStates[index];
          const choices =
            row.action.allowed_decisions || ["approve", "edit", "reject"];
          return (
            <div className="approval-action" key={`${row.interruptId}-${index}`}>
              <div className="approval-action-top">
                <div className="approval-tool">
                  {index + 1}. {toolSummary(row.action.name)} ·{" "}
                  {row.action.name || "unknown"}
                </div>
                <select
                  className="approval-select"
                  value={state.choice}
                  disabled={submitting}
                  aria-label={`${row.action.name || "工具"} 的决定类型`}
                  onChange={(event) => {
                    updateRow(index, {
                      choice: event.target.value as DecisionType,
                      error: "",
                    });
                  }}
                >
                  {choices.map((choice) => (
                    <option key={choice} value={choice}>
                      {DECISION_LABELS[choice] || choice}
                    </option>
                  ))}
                </select>
              </div>
              <div className="approval-description">
                {row.action.description || "该操作需要你的确认后才会执行。"}
              </div>
              <details
                className="approval-args-details"
                open={argsOpen[index] ?? false}
                onToggle={(event) =>
                  setArgsOpen((previous) =>
                    previous.map((open, i) =>
                      i === index ? event.currentTarget.open : open,
                    ),
                  )
                }
              >
                <summary className="approval-args-summary">查看完整参数</summary>
                <pre className="approval-args">{formatArgs(row.action)}</pre>
              </details>
              {state.choice === "edit" && (
                <textarea
                  className="approval-edit"
                  value={state.editedArgs}
                  disabled={submitting}
                  aria-label={`${row.action.name || "工具"} 的编辑参数`}
                  onChange={(event) =>
                    updateRow(index, {
                      editedArgs: event.target.value,
                      error: "",
                    })
                  }
                />
              )}
              {(state.choice === "reject" || state.choice === "respond") && (
                <textarea
                  className="approval-reject"
                  value={state.rejectMessage}
                  disabled={submitting}
                  placeholder="拒绝原因（可选）"
                  aria-label={`${row.action.name || "工具"} 的拒绝原因`}
                  onChange={(event) =>
                    updateRow(index, { rejectMessage: event.target.value })
                  }
                />
              )}
              {state.error && (
                <div className="approval-field-error" role="alert">
                  {state.error}
                </div>
              )}
            </div>
          );
        })}
        {!hasRows && (
          <div className="approval-copy">没有可展示的操作，请刷新会话后重试。</div>
        )}
      </div>
      <button
        type="button"
        className="approval-submit"
        disabled={submitting || !hasRows}
        onClick={() => void handleSubmit()}
      >
        提交决定并继续
      </button>
    </div>
  );
}
