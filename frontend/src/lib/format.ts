import type { CSSProperties } from "react";

/** 时间与用户展示工具。 */

function toDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function safeDate(value: string | number | null | undefined): Date | null {
  return toDate(value);
}

/** 会话列表时间：今天显示 HH:MM，今年显示 MM-DD，更早显示 YYYY-MM-DD。 */
export function formatConversationTime(
  value: string | number | null | undefined,
): string {
  const date = toDate(value);
  if (!date) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.getFullYear() === now.getFullYear()) {
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** 消息时间戳：一律 HH:MM。 */
export function formatMessageTime(
  value: string | number | null | undefined,
): string {
  const date = toDate(value);
  if (!date) return "";
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function userInitials(name: string, userId = ""): string {
  const source = name.trim() || userId.trim();
  if (!source) return "?";
  return source.slice(0, 2).toUpperCase();
}

/** 按用户 ID 稳定生成头像色相（与旧 setPickerAvatar 的思路一致）。 */
export function avatarHue(userId: string): number {
  let hash = 0;
  for (const char of userId) {
    hash = (hash * 31 + char.charCodeAt(0)) % 100000;
  }
  return hash % 360;
}

export function avatarStyle(userId: string): CSSProperties {
  const hue = avatarHue(userId);
  return {
    backgroundColor: `hsl(${hue} 32% 90%)`,
    color: `hsl(${hue} 45% 28%)`,
  };
}
