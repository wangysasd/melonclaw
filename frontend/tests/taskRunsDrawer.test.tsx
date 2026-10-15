import { App } from "antd";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { taskApi, type Task, type TaskRun } from "../src/api/tasks";
import { TaskRunsDrawer } from "../src/components/tasks/TaskRunsDrawer";

vi.mock("../src/api/tasks", () => ({ taskApi: { get: vi.fn(), history: vi.fn(), run: vi.fn(), cancel: vi.fn(), action: vi.fn(), enable: vi.fn() } }));

function run(id: string, taskId = "A", version = 2): TaskRun {
  return { id, task_id: taskId, status: "succeeded", created_at: `2026-10-${version === 2 ? "15" : version === 1 ? "14" : "13"}T01:00:00Z`,
    started_at: "2026-10-15T01:00:02Z", finished_at: "2026-10-15T01:00:05Z", scheduled_for: "2026-10-15T01:00:00Z",
    result_is_partial: false, result_markdown: `REPORT-${id}`,
    config_snapshot: { task_version: version, model_display_name: `执行模型-${id}`,
      definition: { name: taskId, instruction: `要求-${id}`, model_id: "custom:test", schedule: { kind: "daily", local_time: "09:00" } } } };
}

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
  const original = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => original(element));
  vi.mocked(taskApi.get).mockImplementation(async id => ({ id, name: `任务-${id}` } as Task));
  vi.mocked(taskApi.history).mockImplementation(async id => ({ items: [run(`${id}-latest`, id)], total: 1 }));
  vi.mocked(taskApi.run).mockImplementation(async id => run(id, id[0]));
});

it("shows historical snapshots and errors without report bodies, retains a chosen run on refresh, and resets selection on page change", async () => {
  const latest = run("A-latest");
  const older = { ...run("A-older", "A", 1), status: "failed" as const, error_code: "model_timeout", error_message: "模型请求超时" };
  const oldest = run("A-oldest", "A", 0);
  vi.mocked(taskApi.history).mockImplementation(async (_id, page) => ({ items: page === 1 ? [latest, older] : [oldest], total: 21 }));
  vi.mocked(taskApi.run).mockImplementation(async id => ({ "A-latest": latest, "A-older": older, "A-oldest": oldest })[id]!);
  const view = render(<App><TaskRunsDrawer id="A" revision={0} onClose={vi.fn()} onChanged={vi.fn()} /></App>);
  const drawer = screen.getByRole("dialog", { name: "运行记录" });
  await within(drawer).findByText("执行模型-A-latest");
  fireEvent.click(within(drawer).getByRole("button", { name: "查看 2026/10/14 09:00:00 的运行记录" }));
  await within(drawer).findByText("执行模型-A-older");
  expect(within(drawer).getByText("v1")).toBeTruthy();
  expect(within(drawer).getByText("模型请求超时")).toBeTruthy();
  expect(within(drawer).getByText("model_timeout")).toBeTruthy();
  fireEvent.click(within(drawer).getByRole("button", { name: "本次执行要求" }));
  expect(within(drawer).getByText("要求-A-older")).toBeTruthy();
  expect(screen.queryByText("REPORT-A-older")).toBeNull();
  view.rerender(<App><TaskRunsDrawer id="A" revision={1} onClose={vi.fn()} onChanged={vi.fn()} /></App>);
  await waitFor(() => expect(taskApi.history).toHaveBeenCalledTimes(2));
  await within(drawer).findByText("执行模型-A-older");
  fireEvent.click(within(drawer).getByTitle("Next Page"));
  await within(drawer).findByText("执行模型-A-oldest");
  expect(taskApi.history).toHaveBeenLastCalledWith("A", 2, expect.any(AbortSignal));
  expect(within(drawer).getByText("v0")).toBeTruthy();
}, 10000);

it("cancels only the selected run and waits for the executor to confirm its final state", async () => {
  let current = { ...run("A-latest"), status: "running" as TaskRun["status"], finished_at: null };
  const changed = vi.fn();
  vi.mocked(taskApi.run).mockImplementation(async () => current);
  vi.mocked(taskApi.cancel).mockImplementation(async () => {
    current = { ...current, cancel_requested_at: "2026-10-15T01:01:00Z" };
    return current;
  });
  render(<App><TaskRunsDrawer id="A" revision={0} onClose={vi.fn()} onChanged={changed} /></App>);
  fireEvent.click(await screen.findByRole("button", { name: "取消运行" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认取消" }));
  await waitFor(() => expect(taskApi.cancel).toHaveBeenCalledWith("A-latest", expect.any(String)));
  await screen.findByRole("button", { name: "取消请求已提交" });
  expect((screen.getByRole("button", { name: "取消请求已提交" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("region", { name: "本次运行" }).textContent).toContain("执行中");
  expect(changed).toHaveBeenCalledOnce();
  expect(taskApi.enable).not.toHaveBeenCalled();
  expect(taskApi.action).not.toHaveBeenCalled();
});

it.each(["history", "run"])("rejects a %s response belonging to another task", async layer => {
  if (layer === "history") vi.mocked(taskApi.history).mockResolvedValue({ items: [run("other", "B")], total: 1 });
  else vi.mocked(taskApi.run).mockResolvedValue(run("other", "B"));
  render(<App><TaskRunsDrawer id="A" revision={0} onClose={vi.fn()} onChanged={vi.fn()} /></App>);
  await screen.findByText(layer === "history" ? "运行记录不属于当前任务，请刷新后重试。" : "该运行不属于当前任务，请重新选择。");
  expect(screen.queryByText("执行模型-other")).toBeNull();
  expect(screen.queryByText("要求-other")).toBeNull();
});

it("ignores a late run response when the drawer switches to another task", async () => {
  let settle!: (value: TaskRun) => void;
  let oldSignal: AbortSignal | undefined;
  vi.mocked(taskApi.run).mockImplementation(async (id, signal) => {
    if (id === "A-latest") { oldSignal = signal; return new Promise(resolve => { settle = resolve; }); }
    return run(id, "B");
  });
  const props = { revision: 0, onClose: vi.fn(), onChanged: vi.fn() };
  const view = render(<App><TaskRunsDrawer key="A" id="A" {...props} /></App>);
  await waitFor(() => expect(taskApi.run).toHaveBeenCalledWith("A-latest", expect.any(AbortSignal)));
  view.rerender(<App><TaskRunsDrawer key="B" id="B" {...props} /></App>);
  await screen.findByText("执行模型-B-latest");
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => { settle(run("A-latest")); });
  expect(screen.queryByText("执行模型-A-latest")).toBeNull();
  expect(screen.getByText("任务-B")).toBeTruthy();
});
