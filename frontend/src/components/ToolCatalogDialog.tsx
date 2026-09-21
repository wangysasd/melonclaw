import { useEffect } from "react";

import { Icon } from "./Icon";
import {
  TOOL_CATALOG,
  TOOL_CATEGORIES,
  type ToolCategory,
  type ToolDefinition,
} from "../lib/toolCatalog";

interface ToolCatalogDialogProps {
  open: boolean;
  mcpServers: string[];
  onClose: () => void;
}

function ToolCard({ tool }: { tool: ToolDefinition }) {
  return (
    <li className="tool-catalog-item">
      <span className="tool-catalog-icon" aria-hidden="true">
        <Icon name={tool.icon} size={17} />
      </span>
      <span className="tool-catalog-item-copy">
        <span className="tool-catalog-item-heading">
          <strong>{tool.label}</strong>
          <code>{tool.name}</code>
        </span>
        <span className="tool-catalog-item-description">{tool.description}</span>
      </span>
      <span className={`tool-catalog-badge is-${tool.availability}`}>
        {tool.availabilityLabel}
      </span>
    </li>
  );
}

function CategorySection({ category }: { category: ToolCategory }) {
  const tools = TOOL_CATALOG.filter((tool) => tool.category === category.id);
  return (
    <section className="tool-catalog-section" aria-labelledby={`tool-category-${category.id}`}>
      <div className="tool-catalog-section-heading">
        <span className="tool-catalog-category-icon" aria-hidden="true">
          <Icon name={category.icon} size={17} />
        </span>
        <span>
          <h3 id={`tool-category-${category.id}`}>{category.label}</h3>
          <p>{category.description}</p>
        </span>
        <span className="tool-catalog-count">{tools.length}</span>
      </div>
      <ul className="tool-catalog-list">
        {tools.map((tool) => <ToolCard key={tool.name} tool={tool} />)}
      </ul>
    </section>
  );
}

export function ToolCatalogDialog({
  open,
  mcpServers,
  onClose,
}: ToolCatalogDialogProps) {
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="tool-catalog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="tool-catalog-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tool-catalog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="tool-catalog-header">
          <div>
            <p className="tool-catalog-eyebrow">MELONCLAW CAPABILITIES</p>
            <h2 id="tool-catalog-title">系统工具目录</h2>
            <p className="tool-catalog-intro">
              当前 Agent 可调用的固定工具，以及运行时接入的外部服务。
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭工具目录" title="关闭">
            <Icon name="x" size={17} />
          </button>
        </header>

        <div className="tool-catalog-summary" aria-label="工具目录摘要">
          <span><strong>{TOOL_CATALOG.length}</strong> 个固定工具</span>
          <span><strong>{mcpServers.length}</strong> 个 MCP 服务</span>
          <span className="tool-catalog-summary-note">MCP 工具名称在运行时发现</span>
        </div>

        <div className="tool-catalog-content">
          {TOOL_CATEGORIES.map((category) => (
            <CategorySection key={category.id} category={category} />
          ))}

          <section className="tool-catalog-section tool-catalog-runtime-section" aria-labelledby="tool-runtime-title">
            <div className="tool-catalog-section-heading">
              <span className="tool-catalog-category-icon" aria-hidden="true">
                <Icon name="plug" size={17} />
              </span>
              <span>
                <h3 id="tool-runtime-title">已配置 MCP 服务</h3>
                <p>服务连接后，其工具会自动加入 Agent 工具集。</p>
              </span>
              <span className="tool-catalog-count">{mcpServers.length}</span>
            </div>
            {mcpServers.length > 0 ? (
              <ul className="tool-catalog-server-list">
                {mcpServers.map((server) => (
                  <li key={server}>
                    <Icon name="plug" size={15} />
                    <code>{server}</code>
                    <span className="tool-catalog-server-status">工具运行时发现</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="tool-catalog-empty">当前没有配置 MCP 服务。</p>
            )}
          </section>
        </div>
      </section>
    </div>
  );
}
