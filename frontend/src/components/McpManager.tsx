import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Dropdown, Input, Spin } from "antd";
import { listMcp } from "../api/client";
import { addMcp, deleteMcp, discoverMcpTools, globalMcpState, mcpDetail, stopMcp } from "../api/mcp";
import type { McpServer } from "../types/api";
import { Icon } from "./Icon";
import { McpEditor } from "./McpEditor";
import { McpToolDetails, type McpToolDialog } from "./McpToolDetails";
import "./mcp.css";

function isAbortError(error: unknown): boolean {
  return (error instanceof Error || error instanceof DOMException) && error.name === "AbortError";
}

export function McpManager({ userId, isAdmin, notify }: {
  userId: string; isAdmin: boolean; notify: { success: (s: string) => void; error: (s: string) => void };
}) {
  const { modal } = App.useApp();
  const [items, setItems] = useState<McpServer[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [editor, setEditor] = useState<{ existing?: McpServer; fixedSlug?: string } | null>(null);
  const [toolDialog, setToolDialog] = useState<McpToolDialog | null>(null);
  const alive = useRef(true);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const detailRequest = useRef<AbortController | null>(null);
  const toolRequest = useRef<AbortController | null>(null);
  const reload = useCallback(async (sort = false) => {
    const current = ++generation.current;
    controller.current?.abort();
    controller.current = new AbortController();
    const result = await listMcp({ userId }, controller.current.signal);
    if (alive.current && current === generation.current) setItems(previous => {
      const order = new Map(previous.map((item, index) => [item.id, index]));
      return [...result.items].sort((a, b) => sort ? Number(b.effective_enabled) - Number(a.effective_enabled) || a.display_name.localeCompare(b.display_name)
        : (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity));
    });
  }, [userId]);
  useEffect(() => {
    alive.current = true;
    void reload(true).catch(error => {
      if (alive.current && !isAbortError(error)) notify.error(error instanceof Error ? error.message : "加载失败。");
    });
    return () => {
      alive.current = false;
      controller.current?.abort(); detailRequest.current?.abort(); toolRequest.current?.abort();
    };
  }, [reload, notify]);
  const action = async (operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await operation(); if (alive.current) { await reload(); notify.success("已更新，从下一条消息生效。"); } }
    catch (error) { if (alive.current && !isAbortError(error)) notify.error(error instanceof Error ? error.message : "操作失败。"); }
    finally { if (alive.current) setBusy(false); }
  };
  const edit = async (item: McpServer) => {
    detailRequest.current?.abort();
    const request = new AbortController(); detailRequest.current = request;
    try { const detail = await mcpDetail(item.id, userId, request.signal); if (alive.current && !request.signal.aborted) setEditor({ existing: detail }); }
    catch (error) { if (alive.current && !request.signal.aborted) notify.error(error instanceof Error ? error.message : "加载失败。"); }
  };
  const loadTools = async (item: McpServer) => {
    toolRequest.current?.abort();
    const request = new AbortController(); toolRequest.current = request;
    setToolDialog({ item, loading: true });
    try {
      const result = await discoverMcpTools(item, userId, request.signal, { refresh: true });
      if (alive.current && !request.signal.aborted && toolRequest.current === request) {
        setToolDialog({ item, loading: false, result });
      }
    } catch (error) {
      if (alive.current && !request.signal.aborted && toolRequest.current === request) {
        setToolDialog({ item, loading: false, error: error instanceof Error ? error.message : "工具列表加载失败。" });
      }
    }
  };
  const openTools = (item: McpServer) => {
    toolRequest.current?.abort();
    setToolDialog({ item, loading: false });
    if (item.transport !== "stdio") void loadTools(item);
  };
  const closeTools = () => {
    toolRequest.current?.abort(); toolRequest.current = null; setToolDialog(null);
  };
  const refresh = async () => {
    setRefreshing(true);
    try {
      await reload(true);
    } catch (error) {
      if (alive.current && !isAbortError(error)) notify.error(error instanceof Error ? error.message : "刷新失败。");
    } finally {
      if (alive.current) setRefreshing(false);
    }
  };
  const remove = (item: McpServer) => modal.confirm({ rootClassName: "mcp-confirm", title: item.shadows_global ? "删除个人配置并恢复全局？" : "删除 MCP 配置？",
    content: item.shadows_global ? "个人连接及凭据将删除，恢复全局来源，但保留你的使用偏好。" : "配置及凭据将删除。删除个人配置后若无可用全局项，将不再提供此服务。",
    okText: "删除", cancelText: "取消", okButtonProps: { danger: true }, onOk: () => action(() => deleteMcp(item, userId)) });
  return <div className="resource-section mcp-manager">
    <div className="resource-actions"><Input aria-label="搜索 MCP 服务" placeholder="搜索 MCP 服务" value={query} onChange={e => setQuery(e.target.value)} />
      <Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => { detailRequest.current?.abort(); setEditor({}); }}>创建连接器</Button>
      <Button icon={<Icon name="refresh-cw" size={14} />} loading={refreshing} disabled={busy} aria-label="刷新 MCP 服务列表" onClick={() => void refresh()} />
    </div>
    <p className="resource-hint">配置从下一条消息生效，当前运行不受影响。</p>
    <Spin spinning={busy}>
      {(["global", "user"] as const).map(scope => {
        const visible = items.filter(item => item.scope === scope && `${item.display_name} ${item.slug} ${item.description}`.toLowerCase().includes(query.toLowerCase()));
        if (!visible.length) return null;
        return <section className="skill-group" key={scope}><h2 className="mcp-group-title">{scope === "global" ? "系统共享" : "我的"}</h2>
          <div className="skill-card-grid">{visible.map(item => {
            const disabled = item.shadowed || (item.scope === "global" && !item.enabled);
            const showPrimaryAction = !item.effective_enabled;
            const description = item.description || "暂无用途说明";
            const menus = [
              ...(item.can_edit ? [{ key: "edit", label: "编辑" }] : []),
              ...(item.effective_enabled ? [{ key: "stop", label: "我不使用" }] : []),
              ...(isAdmin && item.scope === "global" ? [{ key: "global", label: item.enabled ? "全员停用" : "全员启用" }] : []),
              ...(item.shadowed ? [{ key: "personal-edit", label: "查看我的配置" }] : []),
              ...(item.can_delete ? [{ key: "delete", label: item.shadows_global ? "删除个人配置并恢复全局" : "删除", danger: true }] : []),
            ];
            return <article className={`skill-card mcp-card${showPrimaryAction ? " has-primary-action" : ""}`} key={item.id}>
              <div className="skill-card-top-actions">
                {showPrimaryAction && <Button
                  className="skill-card-primary-action is-add"
                  icon={<Icon name="plus" size={12} />}
                  disabled={busy || (disabled && !(isAdmin && item.scope === "global" && !item.shadowed))}
                  onClick={() => void action(() => item.scope === "global" && !item.enabled ? globalMcpState(item, userId) : addMcp(item, userId))}
                >
                  {disabled ? item.shadowed ? "已遮蔽" : isAdmin ? "全员启用" : "全员已停用" : "添加到我的服务"}
                </Button>}
                <Dropdown
                  classNames={{ root: "skill-card-menu" }}
                  trigger={["click"]}
                  menu={{ items: menus, onClick: ({ key }) => {
                    if (key === "stop") void action(() => stopMcp(item, userId));
                    if (key === "edit") void edit(item);
                    if (key === "global") void action(() => globalMcpState(item, userId));
                    if (key === "personal-edit") { const own = items.find(row => row.scope === "user" && row.slug === item.slug); if (own) void edit(own); }
                    if (key === "delete") remove(item);
                  } }}
                >
                  <Button
                    type="text"
                    className="skill-card-icon-action skill-card-more"
                    aria-label={`${item.display_name} 菜单`}
                    title="更多操作"
                    disabled={busy}
                  >
                    …
                  </Button>
                </Dropdown>
              </div>
              <div
                className="mcp-card-content"
                role="button"
                tabIndex={0}
                aria-haspopup="dialog"
                aria-label={`查看 MCP 工具：${item.display_name}`}
                onClick={() => openTools(item)}
                onKeyDown={event => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openTools(item);
                  }
                }}
              >
                <header className="skill-card-head">
                  <span className="skill-card-name" title={item.display_name}>{item.display_name}</span>
                  {item.shadowed && <span className="resource-badge">已遮蔽</span>}
                </header>
                <p className="skill-card-desc" title={description}>{description}</p>
                <div className="mcp-card-meta-row">
                  <span className="mcp-card-capsule is-transport">{item.transport.toUpperCase()}</span>
                  <span className={`mcp-card-capsule ${item.effective_enabled ? "is-active" : "is-inactive"}`}>
                    {item.effective_enabled ? "正在使用" : item.unavailable_reason || "未使用"}
                  </span>
                </div>
              </div>
            </article>;
          })}</div>
        </section>;
      })}
      {!items.length && <p className="resource-empty">还没有 MCP 服务。</p>}
    </Spin>
    {toolDialog && <McpToolDetails key={toolDialog.item.id} dialog={toolDialog} onClose={closeTools} onDiscover={() => void loadTools(toolDialog.item)} />}
    {editor && <McpEditor userId={userId} isAdmin={isAdmin} existing={editor.existing} fixedSlug={editor.fixedSlug}
      onClose={() => setEditor(null)} onSaved={() => { setEditor(null); void reload().catch(error => notify.error(String(error))); }} />}
  </div>;
}
