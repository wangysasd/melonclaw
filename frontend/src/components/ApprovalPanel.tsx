import { useId, useMemo, useRef, useState } from "react";

import { Icon } from "./Icon";
import { toolSummary } from "../lib/toolDisplay";
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
 * - 每个操作必须明确选择（approve/edit/reject/respond），默认展开参数详情，
 *   编辑参数（JSON 校验，错误贴近输入框）与拒绝原因输入。
 * - 提交时按 interrupt 分组：单 interrupt 平铺 decisions 数组，
 *   多 interrupt 发送 { interrupt_id, decisions } 分组，保持服务端恢复协议。
 * - 编辑时固定原工具名，不能借编辑替换为未审工具。
 */

const DECISION_LABELS: Record<DecisionType, string> = {
  approve: "允许本次",
  edit: "编辑参数",
  reject: "拒绝",
  respond: "提供结果",
};

function normalizeInterrupts(approval: PendingApproval): ApprovalInterrupt[] {
  if (Array.isArray(approval.interrupts) && approval.interrupts.length > 0) {
    return approval.interrupts;
  }
  return [{ id: approval.id || "", actions: approval.actions || [] }];
}

function formatArgs(action: ApprovalAction): string {
  const value = action.args;
  if (!value) return "{}";
  if (typeof value === "string") return value;
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
  choice: DecisionType | "";
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
  const id = useId();
  const submittingRef = useRef(false);
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
      choice: "",
      editedArgs: formatArgs(row.action),
      rejectMessage: "",
      error: "",
    })),
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [argsOpen, setArgsOpen] = useState<boolean[]>(() =>
    rows.map(() => true),
  );

  const updateRow = (index: number, patch: Partial<RowState>) => {
    setRowStates((previous) =>
      previous.map((state, i) => (i === index ? { ...state, ...patch } : state)),
    );
  };

  const handleSubmit = async () => {
    if (submittingRef.current) return;
    // 先本地校验，全部通过后再组装提交。
    const decisionsByInterrupt = new Map<string, ApprovalDecision[]>(
      interrupts.map((interrupt) => [interrupt.id || "", []]),
    );
    const nextStates = rowStates.map((state) => ({ ...state, error: "" }));
    let hasError = false;

    rows.forEach((row, index) => {
      const state = rowStates[index];
      const allowed = row.action.allowed_decisions ?? ["approve", "edit", "reject"];
      if (!state.choice || !allowed.includes(state.choice)) {
        nextStates[index].error = "请明确选择本次操作的处理方式。";
        hasError = true;
        return;
      }
      let decision: ApprovalDecision;
      if (state.choice === "approve") {
        decision = { type: "approve" };
      } else if (state.choice === "reject" || state.choice === "respond") {
        if (state.choice === "respond" && !state.rejectMessage.trim()) {
          nextStates[index].error = "请填写要返回给助手的结果。";
          hasError = true;
          return;
        }
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
    setRowStates(nextStates);

    const decisions: ApprovalDecisions =
      interrupts.length === 1
        ? (decisionsByInterrupt.get(interrupts[0].id || "") ?? [])
        : interrupts.map((interrupt) => ({
            interrupt_id: interrupt.id || "",
            decisions: decisionsByInterrupt.get(interrupt.id || "") || [],
          }));

    submittingRef.current = true;
    setSubmitError("");
    setSubmitting(true);
    try {
      await onSubmit(decisions);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "提交未成功，请重试。");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const hasRows = rows.length > 0;

  return (
    <section className="approval-panel" aria-labelledby={`${id}-title`} aria-busy={submitting}>
      <div className="approval-title" id={`${id}-title`} role="status">
        <Icon name="shield-check" size={19} />
        等待你确认 {rows.length} 项操作
      </div>
      <div className="approval-copy">
        助手已暂停。请查看操作内容并逐项选择；允许仅对本次请求生效，拒绝后助手会收到你的反馈。
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
              </div>
              <div className="approval-choices" role="group" aria-label={`第 ${index + 1} 项 ${row.action.name} 的处理方式`}>
                  {choices.map((choice) => (
                    <button type="button" key={choice} className="approval-choice"
                      aria-pressed={state.choice === choice} disabled={submitting}
                      onClick={() => updateRow(index, { choice, error: "" })}>
                      {DECISION_LABELS[choice] || choice}
                    </button>
                  ))}
              </div>
              <div className="approval-description">
                {row.action.description || "该操作需要你的确认后才会执行。"}
              </div>
              <details
                className="approval-args-details"
                open={argsOpen[index] ?? false}
                onToggle={(event) => {
                  const expanded = event.currentTarget.open;
                  setArgsOpen((previous) =>
                    previous.map((open, i) =>
                      i === index ? expanded : open,
                    ),
                  );
                }}
              >
                <summary className="approval-args-summary">操作内容与参数</summary>
                <pre className="approval-args">{formatArgs(row.action)}</pre>
              </details>
              {state.choice === "edit" && (
                <textarea
                  className="approval-edit"
                  value={state.editedArgs}
                  disabled={submitting}
                  aria-invalid={Boolean(state.error)}
                  aria-describedby={state.error ? `${id}-error-${index}` : undefined}
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
                  placeholder={state.choice === "respond" ? "填写提供给助手的结果（必填）" : "拒绝原因（可选）"}
                  aria-label={`${row.action.name || "工具"} 的${state.choice === "respond" ? "返回结果" : "拒绝原因"}`}
                  aria-invalid={Boolean(state.error)}
                  aria-describedby={state.error ? `${id}-error-${index}` : undefined}
                  onChange={(event) =>
                    updateRow(index, { rejectMessage: event.target.value })
                  }
                />
              )}
              {state.error && (
                <div className="approval-field-error" id={`${id}-error-${index}`} role="alert">
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
      {submitError ? <p className="approval-field-error" role="alert">{submitError}</p> : null}
      <button
        type="button"
        className="approval-submit"
        disabled={submitting || !hasRows || rowStates.some((state) => !state.choice)}
        onClick={() => void handleSubmit()}
      >
        {submitting ? "正在提交决定…" : `提交 ${rows.length} 项决定并继续`}
      </button>
    </section>
  );
}
