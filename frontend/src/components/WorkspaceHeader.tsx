import type { ReactNode } from "react";

import { Icon, type IconName } from "./Icon";

interface WorkspaceHeaderProps {
  title: string;
  icon?: IconName;
  actions?: ReactNode;
}

/** One compact page header for workspace routes; business controls stay with each page. */
export function WorkspaceHeader({ title, icon, actions }: WorkspaceHeaderProps) {
  return (
    <header className="workspace-header">
      <div className="workspace-header-title">
        {icon ? <Icon name={icon} size={16} /> : null}
        <h1>{title}</h1>
      </div>
      {actions ? <div className="workspace-header-actions">{actions}</div> : null}
    </header>
  );
}
