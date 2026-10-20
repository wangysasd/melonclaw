import type { MenuProps } from "antd";
import { Icon, type IconName } from "./Icon";

const menuIcons: Record<string, IconName> = {
  personal: "user-switch", add: "plus", stop: "x", global: "users",
  download: "arrow-up", update: "upload", "remote-update": "globe-2",
  edit: "pencil-line", "personal-edit": "user-switch", delete: "trash-2",
};

/** Shared presentation only; actions, permissions and disabled states stay with callers. */
export function resourceMenuItems(items: NonNullable<MenuProps["items"]>): NonNullable<MenuProps["items"]> {
  const result: NonNullable<MenuProps["items"]> = [];
  let previousGroup = "";
  for (const item of items) {
    if (!item || item.type === "divider") continue;
    const key = String(item.key);
    const group = key === "delete" ? "danger" : ["personal", "add", "stop", "global"].includes(key) ? "usage" : "maintenance";
    if (result.length && group !== previousGroup) result.push({ type: "divider" });
    previousGroup = group;
    const name = menuIcons[key];
    result.push({ ...item, ...(name ? { icon: <Icon name={name} size={16} rotate={key === "download" ? 180 : 0} /> } : {}) });
  }
  return result;
}
