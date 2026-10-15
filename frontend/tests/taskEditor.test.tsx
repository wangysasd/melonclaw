import { App } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TaskEditor } from "../src/components/tasks/TaskEditor";
import { taskApi, type Task } from "../src/api/tasks";
import { ApiError } from "../src/api/client";
const mocks = vi.hoisted(() => ({ models: [
  { id: "custom:personal", display_name: "个人默认", available: true, scope: "user", is_default: true },
  { id: "custom:test", display_name: "系统默认", available: true, scope: "global", is_default: true },
  { id: "custom:chat", display_name: "聊天选择", available: true, scope: "global", is_default: false },
] }));
vi.mock("../src/state/session", () => ({ useSession: () => ({ projects: [], selectedModelId: "custom:chat", modelOptions: mocks.models }) }));
vi.mock("../src/api/tasks", () => ({ taskApi: { save: vi.fn() } }));
beforeEach(() => Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) }));
beforeEach(() => { const original = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => original(element)); });
afterEach(cleanup);
beforeEach(() => {
  vi.mocked(taskApi.save).mockReset();
  mocks.models[1].available = true;
  mocks.models[1].is_default = true;
});

it("saves a concrete manual definition and reuses the key after ambiguous network failure", async () => {
  const onSaved = vi.fn();
  vi.mocked(taskApi.save).mockRejectedValueOnce(new ApiError(0, "连接中断")).mockResolvedValueOnce({ task: { id: "task-1" } as Task, run: null });
  render(<App><TaskEditor onClose={() => {}} onSaved={onSaved} /></App>);
  fireEvent.change(screen.getByLabelText("任务名称"), { target: { value: "每周研究" } });
  fireEvent.change(screen.getByLabelText("任务要求"), { target: { value: "整理公开信息" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并运行" }));
  await screen.findByText("连接中断");
  fireEvent.click(screen.getByRole("button", { name: "保存并运行" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("task-1", undefined));
  const [first, second] = vi.mocked(taskApi.save).mock.calls;
  expect(first[1]).toEqual(second[1]);
  expect(first[1]).toMatchObject({ run_now: true, enabled: true, definition: { model_id: "custom:test", schedule: { kind: "manual" } } });
  expect(first[1]).not.toHaveProperty("user_id");
});

it("keeps an automatically paused task paused when editing its instruction", async () => {
  vi.mocked(taskApi.save).mockResolvedValue({ task: { id: "paused-task" } as Task, run: null });
  const task = { id: "paused-task", version: 2, status: "paused", definition: {
    name: "暂停任务", instruction: "旧要求", model_id: "custom:chat", schedule: { kind: "manual" },
  } } as Task;
  render(<App><TaskEditor task={task} onClose={() => {}} onSaved={() => {}} /></App>);
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
  expect(screen.queryByText("关联项目")).toBeNull();
  expect(screen.queryByText("Skill")).toBeNull();
  expect(screen.queryByText("计划时区")).toBeNull();
  fireEvent.change(screen.getByLabelText("任务要求"), { target: { value: "新要求" } });
  fireEvent.click(screen.getByRole("button", { name: /^保\s*存$/ }));
  await waitFor(() => expect(taskApi.save).toHaveBeenCalledOnce());
  expect(vi.mocked(taskApi.save).mock.calls[0][1]).toMatchObject({ enabled: false, definition: { instruction: "新要求", model_id: "custom:chat" } });
});

it.each(["missing", "disabled"])("requires an explicit model when the system default is %s", async (state) => {
  mocks.models[1].is_default = state !== "missing";
  mocks.models[1].available = state !== "disabled";
  render(<App><TaskEditor onClose={() => {}} onSaved={() => {}} /></App>);
  fireEvent.change(screen.getByLabelText("任务名称"), { target: { value: "新任务" } });
  fireEvent.change(screen.getByLabelText("任务要求"), { target: { value: "整理公开信息" } });
  fireEvent.click(screen.getByRole("button", { name: /^保\s*存$/ }));
  await waitFor(() => expect(document.querySelector(".ant-form-item-has-error")).toBeTruthy());
  expect(screen.getByText("暂无可用的系统默认模型，请明确选择执行模型或联系管理员设置。")).toBeTruthy();
  expect(taskApi.save).not.toHaveBeenCalled();
});

it.each([false, true])("shows loading only on the requested save action (runNow=%s)", async runNow => {
  let finish!: (value: Awaited<ReturnType<typeof taskApi.save>>) => void;
  vi.mocked(taskApi.save).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<App><TaskEditor onClose={() => {}} onSaved={() => {}} /></App>);
  fireEvent.change(screen.getByLabelText("任务名称"), { target: { value: "研究" } });
  fireEvent.change(screen.getByLabelText("任务要求"), { target: { value: "整理公开信息" } });
  const save = screen.getByRole("button", { name: /^保\s*存$/ }) as HTMLButtonElement;
  const run = screen.getByRole("button", { name: "保存并运行" }) as HTMLButtonElement;
  fireEvent.click(runNow ? run : save);
  await waitFor(() => expect(taskApi.save).toHaveBeenCalledOnce());
  expect(save.classList.contains("ant-btn-loading")).toBe(!runNow);
  expect(run.classList.contains("ant-btn-loading")).toBe(runNow);
  expect(save.disabled && run.disabled).toBe(true);
  fireEvent.click(runNow ? save : run);
  expect(taskApi.save).toHaveBeenCalledOnce();
  finish({ task: { id: "saved" } as Task, run: null });
  await waitFor(() => expect(save.disabled || run.disabled).toBe(false));
});
