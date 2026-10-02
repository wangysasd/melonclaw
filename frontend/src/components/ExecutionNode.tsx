import type { ReactEventHandler, ReactNode } from "react";

import { Icon, type IconName } from "./Icon";

/**
 * 执行轨迹的统一折叠节点：工具调用和子 Agent 共享同一套摘要行，
 * 通过统一的左对齐摘要行表达执行状态，不额外展示容器/叶子层级。
 */
export function ExecutionNode({
  className,
  detailsClassName,
  open,
  onToggle,
  icon,
  iconClassName,
  title,
  subtitle,
  status,
  children,
}: {
  className: string;
  open: boolean;
  detailsClassName?: string;
  onToggle?: ReactEventHandler<HTMLDetailsElement>;
  icon: IconName;
  iconClassName?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  status?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <details className={className} open={open} onToggle={onToggle}>
      <summary className="execution-node-head">
        <Icon name={icon} size={16} className={`execution-node-icon${iconClassName ? ` ${iconClassName}` : ""}`} />
        <span className="execution-node-title">{title}</span>
        {subtitle ? <span className="execution-node-subtitle">{subtitle}</span> : null}
        {status ? <span className="execution-node-status">{status}</span> : null}
        <Icon name="chevron-right" size={14} className="execution-node-chevron" rotate={open ? 90 : 0} />
      </summary>
      {children ? (
        <div className={`execution-node-details${detailsClassName ? ` ${detailsClassName}` : ""}`}>
          {children}
        </div>
      ) : null}
    </details>
  );
}
