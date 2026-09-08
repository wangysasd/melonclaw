/**
 * localStorage 持久化：保持既有键名，避免用户升级前端后本地状态丢失。
 */
export const USER_STORAGE_KEY = "melonclaw.user_id.v3";
export const TENANT_STORAGE_KEY = "melonclaw.tenant_id.v1";
export const SIDEBAR_STORAGE_KEY = "melonclaw.sidebar_collapsed.v1";

export const conversationStorageKey = (userId: string): string =>
  `melonclaw.conversation_id.${userId}`;

export const projectStorageKey = (userId: string): string =>
  `melonclaw.project_id.${userId}`;

export function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    // 隐私模式或存储被禁用时静默降级为无记忆状态。
    return null;
  }
}

export function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 同上：写入失败不影响主流程。
  }
}
