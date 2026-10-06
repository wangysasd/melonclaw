/// <reference types="vite/client" />

declare const __MELONCLAW_NAME__: string;
declare const __MELONCLAW_IS_RMS_BRAND__: boolean;

interface ImportMetaEnv {
  /** 编译期注入的 API 基地址；为空表示同源（开发代理或生产反代）。 */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
