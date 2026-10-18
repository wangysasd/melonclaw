import { WorkspaceHeader } from "./WorkspaceHeader";

interface PlaceholderViewProps {
  title: "AMP" | "Mindera";
}

/** 尚未接入实际功能的全局导航占位页。 */
export function PlaceholderView({ title }: PlaceholderViewProps) {
  return (
    <main className="placeholder-view" aria-label={`${title}页面`}>
      <WorkspaceHeader title={title} />
      <div className="placeholder-view-content">{title} 页面</div>
    </main>
  );
}
