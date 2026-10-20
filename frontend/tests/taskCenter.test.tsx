import { App } from "antd";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import TaskCenter from "../src/components/tasks/TaskCenter";
import { taskApi, type Task, type TaskRun } from "../src/api/tasks";

vi.mock("../src/state/session", () => ({ useSession: () => ({ modelOptions: [{ id: "custom:test", display_name: "任务执行模型" }] }) }));
vi.mock("../src/api/tasks", () => ({ taskApi: {
  list: vi.fn(), status: vi.fn(), get: vi.fn(), history: vi.fn(), run: vi.fn(), action: vi.fn(),
} }));

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({
    matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  })) });
  const original = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => original(element));
  const task = (id: string) => ({ id, conversation_id: `chat-${id}`, name: id, version: 2, status: "enabled", latest_run: run(id), definition: {
    name: id, instruction: id, model_id: "custom:test", schedule: { kind: "manual" },
  } } as Task);
  const run = (id: string) => ({ id: `run-${id}`, task_id: id, status: "failed", result_is_partial: true,
    result_markdown: `REPORT-${id}`, created_at: "2026-10-14T00:00:00Z" } as TaskRun);
  vi.mocked(taskApi.list).mockResolvedValue({ items: [task("A"), task("B")], total: 2 });
  vi.mocked(taskApi.status).mockResolvedValue({ executor: { ready: true, running: 0, last_scan_at: null, error: null }, search_available: false });
  vi.mocked(taskApi.get).mockImplementation(async id => task(id));
  vi.mocked(taskApi.history).mockImplementation(async id => ({ items: [run(id)], total: 1 }));
  vi.mocked(taskApi.run).mockImplementation(async id => run(id.slice(4)));
  vi.mocked(taskApi.action).mockResolvedValue(run("B"));
});

it("shows separate list columns, Beijing schedule and run times, and compact actions with the original commands in a menu", async () => {
  const open = vi.fn();
  vi.mocked(taskApi.list).mockResolvedValue({ items: [{
    id: "weekly", conversation_id: "chat-weekly", name: "每周行业报告", version: 2, status: "enabled",
    definition: { name: "每周行业报告", instruction: "整理报告", model_id: "custom:test", schedule: { kind: "weekly", weekday: 0, local_time: "09:00" } },
    next_run_at: "2026-10-19T01:00:00Z",
    latest_run: { id: "last", status: "succeeded", created_at: "2026-10-12T00:59:00Z", started_at: "2026-10-12T01:00:00Z" } as TaskRun,
  } as Task], total: 1 });
  render(<App><TaskCenter onClose={vi.fn()} onOpenConversation={open} /></App>);
  const row = (await screen.findByRole("button", { name: "每周行业报告" })).closest("tr")!;
  expect(screen.getAllByRole("columnheader").map(cell => cell.textContent)).toEqual([
    "任务名称", "任务模型", "执行计划", "状态", "最近一次运行", "下一次运行", "操作",
  ]);
  const cells = within(row).getAllByRole("cell");
  expect(cells[1].textContent).toBe("任务执行模型");
  expect(cells[2].textContent).toBe("每周一 09:00");
  expect(cells[4].textContent).toBe("成功2026-10-1209:00");
  expect(cells[5].textContent).toBe("2026-10-1909:00");
  expect(within(cells[6]).getAllByRole("button").map(button => button.textContent?.replace(/\s/g, ""))).toEqual(["运行", "···"]);
  fireEvent.click(within(row).getByRole("button", { name: /更多操作/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "查看会话" }));
  expect(open).toHaveBeenCalledWith("chat-weekly");
});

it("shows why no future run is scheduled and preserves archived action restrictions", async () => {
  const tasks: Task[] = [
    { id: "paused", name: "暂停日报", status: "paused", schedule: { kind: "daily", local_time: "10:30" } },
    { id: "archived", name: "归档任务", status: "archived", schedule: { kind: "daily", local_time: "10:30" } },
    { id: "ended", name: "一次任务", status: "enabled", schedule: { kind: "once", run_at: "2026-10-14T01:00:00Z" } },
    { id: "manual", name: "手动任务", status: "enabled", schedule: { kind: "manual" } },
  ].map(({ schedule, ...task }) => ({ ...task, conversation_id: `chat-${task.id}`, version: 1,
    definition: { name: task.name, instruction: "整理报告", model_id: "custom:test", schedule },
  } as Task));
  vi.mocked(taskApi.list).mockResolvedValue({ items: tasks, total: tasks.length });
  render(<App><TaskCenter onClose={vi.fn()} /></App>);
  await screen.findByText("暂停日报");
  for (const [index, next] of ["已暂停", "已归档", "计划已结束", "手动运行"].entries()) {
    const row = screen.getByText(tasks[index].name).closest("tr")!;
    const cells = row.querySelectorAll("td");
    expect(cells[4].textContent).toBe("尚未运行");
    expect(cells[5].textContent).toBe(next);
    const buttons = Array.from(cells[6].querySelectorAll("button"));
    expect(buttons).toHaveLength(2);
    if (tasks[index].status === "archived") {
      expect(buttons[0].disabled).toBe(true);
      fireEvent.click(buttons[1]);
      for (const name of ["编辑", "恢复", "归档"]) expect((await screen.findByRole("menuitem", { name })).getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(await screen.findByRole("menuitem", { name: "查看会话" }));
    }
  }
  expect(screen.getAllByText("每天 10:30")).toHaveLength(2);
  expect(screen.getByText("一次 2026-10-14 09:00")).toBeTruthy();
});

it("opens task names directly in their fixed conversations and runs the current configuration from the list", async () => {
  const open = vi.fn();
  render(<App><TaskCenter onClose={vi.fn()} onOpenConversation={open} /></App>);
  fireEvent.click(await screen.findByRole("button", { name: "A" }));
  expect(open).toHaveBeenCalledWith("chat-A");
  expect(taskApi.get).not.toHaveBeenCalled();
  expect(taskApi.history).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "返回列表" })).toBeNull();
  expect(screen.queryByText("REPORT-A")).toBeNull();
  const row = screen.getByText("B").closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: /^运\s*行$/ }));
  await waitFor(() => expect(taskApi.action).toHaveBeenCalledOnce());
  expect(vi.mocked(taskApi.action).mock.calls[0]).toEqual([expect.objectContaining({ id: "B", version: 2 }), "runs", expect.any(String)]);
  expect(open).toHaveBeenLastCalledWith("chat-B");
  expect(screen.queryByText("REPORT-B")).toBeNull();
});

it("focuses cancel in the archive dialog and archives the selected task only after explicit confirmation", async () => {
  render(<App><TaskCenter onClose={vi.fn()} /></App>);
  const row = (await screen.findByText("A")).closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: /更多操作/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "归档" }));
  let dialog = await screen.findByRole("dialog");
  expect(dialog.textContent).toContain("归档任务？");
  expect(within(dialog).getByText("A")).toBeTruthy();
  const cancel = within(dialog).getByRole("button", { name: /^取\s*消$/ });
  await waitFor(() => expect(document.activeElement).toBe(cancel));
  expect(taskApi.action).not.toHaveBeenCalled();
  fireEvent.click(cancel);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(taskApi.action).not.toHaveBeenCalled();
  fireEvent.click(within(row).getByRole("button", { name: /更多操作/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "归档" }));
  dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认归档" }));
  await waitFor(() => expect(taskApi.action).toHaveBeenCalledOnce());
  expect(taskApi.action).toHaveBeenCalledWith(expect.objectContaining({ id: "A", version: 2 }), "archive", expect.any(String));
});

it("opens the latest run in a drawer and preserves list search and page when it closes", async () => {
  vi.mocked(taskApi.list).mockResolvedValue({ items: [(await taskApi.get("A", new AbortController().signal))], total: 40 });
  vi.mocked(taskApi.get).mockClear();
  render(<App><TaskCenter onClose={vi.fn()} /></App>);
  await screen.findByText("A");
  const search = screen.getByPlaceholderText("搜索任务名称");
  fireEvent.change(search, { target: { value: "日报" } });
  await waitFor(() => expect(taskApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ name: "日报", page: 1 }), expect.any(AbortSignal)));
  await screen.findByText("A");
  fireEvent.click(screen.getByTitle("2"));
  await waitFor(() => expect(taskApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ name: "日报", page: 2 }), expect.any(AbortSignal)));
  await waitFor(() => expect(document.querySelector(".ant-pagination-item-active")?.textContent).toBe("2"));
  fireEvent.click(await screen.findByRole("button", { name: /^查看A的运行记录/ }));
  await waitFor(() => expect(taskApi.get).toHaveBeenCalledWith("A", expect.any(AbortSignal)));
  // Ant Design 测试环境复用 test-id；列表控件和抽屉标题会撞 ID，标题单独验证。
  const drawer = await screen.findByRole("dialog");
  expect(within(drawer).getByText("运行记录")).toBeTruthy();
  await waitFor(() => expect(taskApi.run).toHaveBeenCalledWith("run-A", expect.any(AbortSignal)));
  expect(screen.queryByText("REPORT-A")).toBeNull();
  fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect((screen.getByPlaceholderText("搜索任务名称") as HTMLInputElement).value).toBe("日报");
  expect(taskApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ name: "日报", page: 2 }), expect.any(AbortSignal));
}, 10000);

it("keeps each task's action pending independently when requests finish out of order", async () => {
  const finish = new Map<string, (value: TaskRun) => void>();
  vi.mocked(taskApi.action).mockImplementation(task => new Promise(resolve => { finish.set(task.id, resolve); }));
  render(<App><TaskCenter onClose={vi.fn()} /></App>);
  const rowA = (await screen.findByText("A")).closest("tr")!;
  const rowB = screen.getByText("B").closest("tr")!;
  const runA = within(rowA).getByRole("button", { name: /^运\s*行$/ }) as HTMLButtonElement;
  const runB = within(rowB).getByRole("button", { name: /^运\s*行$/ }) as HTMLButtonElement;
  fireEvent.click(runA);
  await waitFor(() => expect(runA.disabled).toBe(true));
  expect(runB.disabled).toBe(false);
  fireEvent.click(runB);
  await waitFor(() => expect(taskApi.action).toHaveBeenCalledTimes(2));
  finish.get("A")!({ id: "run-A", task_id: "A" } as TaskRun);
  const currentRun = (id: string) => within(screen.getByText(id).closest("tr")!).getByRole("button", { name: /运\s*行$/ }) as HTMLButtonElement;
  await waitFor(() => expect(currentRun("A").disabled).toBe(false));
  expect(currentRun("B").disabled).toBe(true);
  expect(currentRun("B").classList.contains("ant-btn-loading")).toBe(true);
  finish.get("B")!({ id: "run-B", task_id: "B" } as TaskRun);
  await waitFor(() => expect(currentRun("B").disabled).toBe(false));
});
