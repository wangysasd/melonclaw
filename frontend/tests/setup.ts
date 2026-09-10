import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
afterEach(cleanup);

// antd X Sender 使用 ResizeObserver；jsdom 没有布局引擎，提供最小兼容桩以保留行为测试。
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
