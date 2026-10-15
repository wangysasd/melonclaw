import { zonedLocal } from "./schedule";

export const statusNames: Record<string, string> = { enabled: "已启用", paused: "已暂停", archived: "已归档", queued: "排队中", running: "执行中", succeeded: "成功", failed: "失败", cancelled: "已取消" };

const statusColors: Record<string, "success" | "warning" | "processing" | "error" | "default"> = {
  enabled: "success",
  paused: "warning",
  archived: "default",
  queued: "processing",
  running: "processing",
  succeeded: "success",
  failed: "error",
  cancelled: "default",
};

export const time = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }) : "—";

export function StatusTag({ status }: { status: string }) {
  return <span className={`task-status-pill is-${statusColors[status] ?? "default"}`}>{statusNames[status] ?? status}</span>;
}

export function TaskTime({ value }: { value: string }) {
  const [date, clock] = zonedLocal(value).split("T");
  return <span className="task-time"><span>{date}</span><span>{clock}</span></span>;
}
