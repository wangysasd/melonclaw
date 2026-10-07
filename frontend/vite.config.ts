import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
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
const envRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  // 只把助手名称和已归一化的品牌开关注入浏览器代码，不暴露其他环境变量。
  const env = loadEnv(mode, envRoot, ["MELONCLAW_NAME", "brand"]);
  const brandEnv = mode === "production" ? loadEnv("prod", envRoot, ["brand"]) : env;
  const isRmsBrand = (process.env.brand ?? brandEnv.brand)?.trim().toLowerCase() === "rms";
  return {
    define: {
      __MELONCLAW_NAME__: JSON.stringify((process.env.MELONCLAW_NAME ?? env.MELONCLAW_NAME)?.trim() || "MelonClaw"),
      __MELONCLAW_IS_RMS_BRAND__: JSON.stringify(isRmsBrand),
    },
    plugins: [
      react(),
      {
        name: "melonclaw-brand-html",
        transformIndexHtml(html) {
          const title = isRmsBrand ? "RMS · 投研助手" : "MelonClaw · 瓜爪助手";
          const favicon = isRmsBrand
            ? "/assets/brand/melonclaw-favicon-rms.png"
            : "/assets/brand/melonclaw-favicon.png";
          return html
            .replace("__MELONCLAW_PAGE_TITLE__", title)
            .replace("__MELONCLAW_FAVICON__", favicon);
        },
      },
    ],
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
  };
});
