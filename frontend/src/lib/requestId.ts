/** 请求幂等键与页面事件标识；HTTP 页面也必须返回标准 UUID v4。 */
export function createRequestId(): string {
  const crypto = globalThis.crypto;
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
  if (typeof crypto?.getRandomValues !== "function") {
    throw new Error("当前浏览器不支持安全随机数，请使用现代浏览器");
  }

  // randomUUID 仅在安全上下文开放，getRandomValues 也可用于普通 HTTP 页面。
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
