import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { INITIAL_CHAT_STATE, reducer, type ChatMessage } from "../src/hooks/useChatStream";
import { useVisibleRefresh } from "../src/hooks/useVisibleRefresh";

beforeEach(() => { Object.defineProperty(document, "hidden", { configurable: true, value: false }); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });
function taskMessage(status: "queued" | "running" | "succeeded" = "running"): ChatMessage {
  return { id: "task-assistant", seq: 4, role: "assistant", content: "任务报告", status: status === "succeeded" ? "completed" : "pending", markdown: true, events: [], assistantSteps: [], artifacts: [], phases: [],
    taskContext: { task_run: { run_id: "run", task_id: "task", name: "日报", status, trigger_type: "schedule", queued_at: "2026-10-15T01:00:00Z", scheduled_for: null, error_message: null, result_is_partial: status !== "succeeded", cancel_requested: false } } };
}
it("merges task updates without replacing live chat text or redirecting its stream events", () => {
  const live: ChatMessage = { ...taskMessage(), id: "normal", seq: 2, taskContext: undefined, status: "streaming", content: "尚未入库的追问回复" };
  const task = taskMessage();
  const state = { ...INITIAL_CHAT_STATE, conversationId: "chat", messages: [live, task] };
  const freshTask = taskMessage("succeeded");
  let next = reducer(state, { type: "taskHistorySynced", conversationId: "chat", messages: [
    { ...live, status: "pending", content: "旧快照" }, freshTask,
  ] });
  expect(next.messages[0].content).toBe("尚未入库的追问回复");
  expect(next.messages[1].status).toBe("completed");
  next = reducer(next, { type: "text", text: "新增" });
  next = reducer(next, { type: "displayEvent", event: { type: "model_usage" } });
  expect(next.messages[0].content).toBe("尚未入库的追问回复新增");
  expect(next.messages[0].events).toHaveLength(1);
  expect(next.messages[1].content).toBe("任务报告");
  expect(next.messages[1].events).toHaveLength(0);
  const unchanged = reducer(next, { type: "taskHistorySynced", conversationId: "chat", messages: [{ ...freshTask, events: [], assistantSteps: [] }] });
  expect(unchanged.messages[1]).toBe(next.messages[1]);
  expect(reducer(next, { type: "taskHistorySynced", conversationId: "other", messages: [] })).toBe(next);
});
it("associates a task created in the current chat without replacing its messages or live stream", () => {
  const live = { ...taskMessage(), id: "normal", taskContext: undefined, status: "streaming" as const };
  const state = { ...INITIAL_CHAT_STATE, conversationId: "chat", messages: [live] };
  const next = reducer(state, { type: "taskAssociated", conversationId: "chat", taskId: "new-task" });
  expect(next.conversationTaskId).toBe("new-task");
  expect(next.messages).toBe(state.messages);
  expect(reducer(next, { type: "taskAssociated", conversationId: "chat", taskId: "new-task" })).toBe(next);
  expect(reducer(next, { type: "taskAssociated", conversationId: "other", taskId: "other-task" })).toBe(next);
});
it("aborts passive refresh on hidden tabs, resumes once visible, and cancels on unmount", async () => {
  vi.useFakeTimers();
  const requests: AbortSignal[] = [];
  const refresh = vi.fn((signal: AbortSignal) => { requests.push(signal); return new Promise<void>(() => {}); });
  const hook = renderHook(() => useVisibleRefresh(refresh, 3000, true));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(refresh).toHaveBeenCalledOnce();
  Object.defineProperty(document, "hidden", { configurable: true, value: true });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(requests[0].aborted).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(refresh).toHaveBeenCalledOnce();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(refresh).toHaveBeenCalledTimes(2);
  hook.unmount();
  expect(requests[1].aborted).toBe(true);
});
