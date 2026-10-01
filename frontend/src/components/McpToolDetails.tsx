import { useState } from "react";
import { Alert, Button, Input, Modal, Spin } from "antd";
import type { McpServer, McpToolDiscoveryResult } from "../types/api";
import { discoveryStatus } from "../lib/mcpDiscovery";

export interface McpToolDialog {
  item: McpServer; loading: boolean; result?: McpToolDiscoveryResult; error?: string;
}
export function McpToolDetails({ dialog, onClose, onDiscover }: {
  dialog: McpToolDialog; onClose: () => void; onDiscover: () => void;
}) {
  const [query, setQuery] = useState("");
  const { item, loading, result, error } = dialog;
  const status = loading ? "checking" : result ? discoveryStatus(result) : "unknown";
  const tools = result?.tools.filter(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(query.toLowerCase())) ?? [];
  return <Modal className="mcp-tool-details" open width="min(960px, calc(100vw - 32px))"
    title={<div className="mcp-tool-details-title"><strong>{item.display_name}</strong>
      <span className={`mcp-tool-status-dot is-${status}`} aria-label={`MCP 连接：${loading ? "检测中" : result?.ok ? "成功" : result ? "未成功" : "待检测"}`} /></div>}
    footer={<><Button disabled={loading} onClick={onDiscover}>重新检测{item.transport === "stdio" ? "并启动程序" : ""}</Button><Button onClick={onClose}>关闭</Button></>}
    onCancel={onClose}>
    <div className="mcp-tool-details-info">
      <p className="resource-hint mcp-tool-details-description">{item.effective_enabled ? "正在使用，以下工具按当前白名单提供给 Agent。" : "当前未使用此服务，允许的工具在启用后才会提供给 Agent。"}</p>
      {loading && <div className="mcp-tool-details-loading"><Spin /><span>正在发现工具…</span></div>}
      {error && <Alert type="error" showIcon title={error} />}
      {item.transport === "stdio" && !loading && !result && !error && <div className="mcp-tool-stdio-start">
        <Alert type="warning" showIcon title="发现工具会启动此 MCP 配置中的本地程序。" />
        <Button type="primary" onClick={onDiscover}>发现工具并启动程序</Button>
      </div>}
      {result && !result.ok && <Alert type={result.error_code === "busy" ? "info" : "error"} showIcon title={result.message} />}
      {result?.ok && <>
        <p className="mcp-tool-details-count">允许 {result.enabled_tool_count}/{result.tool_count} 个工具 · 本次目录发现成功</p>
        {result.missing_allowed_tools.length > 0 && <Alert type="warning" showIcon title="部分白名单工具已不存在，其他允许工具仍可使用。"
          description={result.missing_allowed_tools.join("、")} />}
        {result.enabled_tool_count === 0 && <Alert type="warning" showIcon title="当前没有可提供给 Agent 的工具，请检查白名单或服务目录。" />}
      </>}
    </div>
    {result?.ok && <>
      <Input className="mcp-tool-details-search" aria-label="搜索 MCP 工具" placeholder="搜索名称或用途" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="mcp-tool-list-details" aria-label="MCP 工具列表">
        {tools.map(tool => <details className="mcp-tool-description" key={tool.name}>
          <summary><strong>{tool.name}</strong>
            <span className={`mcp-tool-permission${tool.enabled ? " is-allowed" : ""}`}>{tool.enabled ? "已允许" : "未允许"}</span>
          </summary>
          <p>{tool.description || "暂无用途说明"}</p>
        </details>)}
        {!tools.length && <p className="resource-empty">{result.tools.length ? "没有匹配的工具。" : "该服务没有发现工具。"}</p>}
      </div>
    </>}
  </Modal>;
}
