import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端地址：优先 MELONCLAW_API_TARGET，其次 MELONCLAW_PORT，默认 127.0.0.1:8000。
const backendPort = process.env.MELONCLAW_PORT ?? "8000";
const backendTarget =
  process.env.MELONCLAW_API_TARGET ?? `http://127.0.0.1:${backendPort}`;
const frontendPort = Number.parseInt(
  process.env.MELONCLAW_FRONTEND_PORT ?? "8001",
  10,
);
const frontendHost = process.env.MELONCLAW_FRONTEND_HOST ?? "127.0.0.1";

export default defineConfig({
  plugins: [react()],
  server: {
    host: frontendHost,
    port: frontendPort,
    proxy: {
      "/api": {
        target: backendTarget,
        // 保持浏览器侧 Host，FastAPI 不依赖 Host 头；跨域由后端 CORS 中间件兜底。
        changeOrigin: false,
        configure: (proxy) => {
          // SSE 流式响应透传：禁止代理层缓冲，保证事件与 keep-alive 心跳即时到达。
          proxy.on("proxyRes", (proxyRes) => {
            const contentType = proxyRes.headers["content-type"];
            if (contentType && contentType.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  build: {
    sourcemap: false,
  },
});
