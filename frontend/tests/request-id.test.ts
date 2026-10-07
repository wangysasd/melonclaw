import { afterEach, expect, it, vi } from "vitest";
import { AUTH_EVENT, AUTH_STORAGE, notifyAuthChange } from "../src/api/auth";
import { createRequestId } from "../src/lib/requestId";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("uses native randomUUID with its Crypto receiver", () => {
  const crypto = {
    randomUUID() {
      expect(this).toBe(crypto);
      return "12345678-1234-4123-8123-123456789abc";
    },
  };
  vi.stubGlobal("crypto", crypto);
  expect(createRequestId()).toBe("12345678-1234-4123-8123-123456789abc");
});

it("generates distinct UUID v4 request keys when HTTP hides randomUUID", () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
  vi.stubGlobal("crypto", { getRandomValues });
  const first = createRequestId();
  const second = createRequestId();
  expect(first).toMatch(UUID_V4);
  expect(second).toMatch(UUID_V4);
  expect(first).not.toBe(second);
});

it("still broadcasts login changes across tabs without randomUUID", () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
  vi.stubGlobal("crypto", { getRandomValues });
  const listener = vi.fn();
  window.addEventListener(AUTH_EVENT, listener);
  try {
    notifyAuthChange();
    const first = localStorage.getItem(AUTH_STORAGE);
    notifyAuthChange();
    expect(first).toMatch(UUID_V4);
    expect(localStorage.getItem(AUTH_STORAGE)).toMatch(UUID_V4);
    expect(localStorage.getItem(AUTH_STORAGE)).not.toBe(first);
    expect(listener).toHaveBeenCalledTimes(2);
  } finally {
    window.removeEventListener(AUTH_EVENT, listener);
  }
});

it("reports unsupported browsers instead of generating a weak request key", () => {
  vi.stubGlobal("crypto", undefined);
  expect(createRequestId).toThrow("当前浏览器不支持安全随机数");
});
