import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  define: {
    __MELONCLAW_IS_RMS_BRAND__: false,

  },
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"], restoreMocks: true },
});
