import { fileURLToPath } from "node:url";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: {
    __MELONCLAW_IS_RMS_BRAND__: false,
    __MELONCLAW_NAME__: JSON.stringify(
      loadEnv(mode, fileURLToPath(new URL("..", import.meta.url)), "MELONCLAW_NAME").MELONCLAW_NAME?.trim() || "MelonClaw",
    ),
  },
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"], restoreMocks: true },
}));
