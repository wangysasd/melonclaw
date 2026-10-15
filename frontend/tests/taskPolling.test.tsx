import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useTaskPolling } from "../src/components/tasks/useTaskPolling";

afterEach(() => { cleanup(); vi.useRealTimers(); Object.defineProperty(document, "hidden", { configurable: true, value: false }); });

it("aborts hidden and unmounted requests, ignores stale responses, resumes immediately", async () => {
  vi.useFakeTimers();
  let complete: ((value: string) => void) | undefined;
  const signals: AbortSignal[] = [];
  const load = vi.fn((signal: AbortSignal) => { signals.push(signal); return new Promise<string>(resolve => { complete = resolve; }); });
  const { result, unmount } = renderHook(() => useTaskPolling(load, 3000));
  expect(load).toHaveBeenCalledTimes(1);
  act(() => { Object.defineProperty(document, "hidden", { configurable: true, value: true }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(signals[0].aborted).toBe(true);
  await act(async () => { complete?.("stale"); await vi.advanceTimersByTimeAsync(9000); });
  expect(result.current.data).toBeUndefined();
  expect(load).toHaveBeenCalledTimes(1);
  act(() => { Object.defineProperty(document, "hidden", { configurable: true, value: false }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { complete?.("fresh"); });
  expect(result.current.data).toBe("fresh");
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(load).toHaveBeenCalledTimes(3);
  unmount();
  expect(signals[2].aborted).toBe(true);
});

it("changing the task cancels the previous in-flight request", async () => {
  const previous = vi.fn((_signal: AbortSignal) => new Promise<string>(() => {}));
  const next = vi.fn(async (_signal: AbortSignal) => "new task");
  const { result, rerender } = renderHook(({ load }) => useTaskPolling(load, 3000), { initialProps: { load: previous } });
  await act(async () => { rerender({ load: next }); });
  expect(previous.mock.calls[0][0].aborted).toBe(true);
  expect(result.current.data).toBe("new task");
});

it("clears previous query data immediately and removes data after permission failure", async () => {
  vi.useFakeTimers();
  const first = vi.fn(async (_signal: AbortSignal) => "previous owner data");
  const next = vi.fn((_signal: AbortSignal) => new Promise<string>(() => {}));
  const { result, rerender } = renderHook(({ load }) => useTaskPolling(load, 3000), { initialProps: { load: first } });
  await act(async () => {});
  expect(result.current.data).toBe("previous owner data");
  act(() => { rerender({ load: next }); });
  expect(result.current.data).toBeUndefined();
  await act(async () => { rerender({ load: first }); });
  expect(result.current.data).toBe("previous owner data");
  first.mockRejectedValueOnce(new Error("无权限"));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(result.current.data).toBeUndefined();
  expect(result.current.error).toBe("无权限");
});
