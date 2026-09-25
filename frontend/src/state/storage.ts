/** 开发页面的用户和会话选择。 */
export const USER_STORAGE_KEY = "melonclaw.user_id.v3";
export const SIDEBAR_STORAGE_KEY = "melonclaw.sidebar_collapsed.v1";

export const conversationStorageKey = (userId: string): string =>
  `melonclaw.conversation_id.${userId}`;

export const projectStorageKey = (userId: string): string =>
  `melonclaw.project_id.${userId}`;

/** 一个用户只归属一个租户，模型选择按用户隔离。 */
export const modelStorageKey = (userId: string): string =>
  `melonclaw.model_id.${userId}`;

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
