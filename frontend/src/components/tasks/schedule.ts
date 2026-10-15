import type { TaskSchedule } from "../../api/tasks";

export const TASK_TIMEZONE = "Asia/Shanghai";

export function zonedLocal(value: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TASK_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value));
  const part = (name: string) => parts.find(p => p.type === name)?.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

export function localToUtc(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error("请选择完整日期和时间（精确到分钟）");
  const timestamp = Date.parse(`${value}:00+08:00`);
  if (!Number.isFinite(timestamp) || zonedLocal(new Date(timestamp).toISOString()) !== value) throw new Error("日期或时间无效");
  return new Date(timestamp).toISOString();
}

export function scheduleLabel(schedule: TaskSchedule): string {
  if (schedule.kind === "manual") return "手动运行";
  if (schedule.kind === "once") return `一次 · ${schedule.run_at ? zonedLocal(schedule.run_at).replace("T", " ") : ""}`;
  return `${schedule.kind === "daily" ? "每天" : `每周${"一二三四五六日"[schedule.weekday ?? 0]}`} ${schedule.local_time}`;
}
