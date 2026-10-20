import type { ReactNode } from "react";
import { tokens } from "../theme/tokens";

/** Shared presentation for destructive confirmations; callers own the action. */
export function deleteConfirmation({ title, name, metadata, icon, description }: {
  title: string; name: string; metadata: string; icon: ReactNode; description: string;
}) {
  return {
    rootClassName: "delete-confirm-modal", centered: true, width: 440, icon: null,
    styles: { container: { padding: tokens["space-6"] }, body: { padding: 0 } },
    title,
    content: <div className="delete-confirm-content">
      <div className="delete-confirm-resource">{icon}<div><strong>{name}</strong><span>{metadata}</span></div></div>
      <p>{description}</p>
    </div>,
    okText: "确认删除", cancelText: "取消",
    focusable: { autoFocusButton: "cancel" as const }, okButtonProps: { danger: true },
  };
}
