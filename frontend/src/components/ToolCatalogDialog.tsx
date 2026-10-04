import { useEffect, useState, type ReactNode } from "react";
import { listMcp, listSkills } from "../api/client";
import { Icon, type IconName } from "./Icon";
import { TOOL_CATALOG, TOOL_CATEGORIES, type ToolDefinition } from "../lib/toolCatalog";
import type { McpServer, ModelOption, ServiceStatus, SkillOption } from "../types/api";

interface ToolCatalogDialogProps {
  open: boolean;
  userId: string;
  status: ServiceStatus | null;
  modelOptions: ModelOption[];
  onClose: () => void;
}

function Section({ id, title, description, icon, count, children }: {
  id: string; title: string; description: string; icon: IconName;
  count?: number | string; children: ReactNode;
}) {
  return <section className="tool-catalog-section" aria-labelledby={`catalog-${id}`}>
    <div className="tool-catalog-section-heading">
      <span className="tool-catalog-category-icon"><Icon name={icon} size={16} /></span>
      <span><h3 id={`catalog-${id}`}>{title}</h3><p>{description}</p></span>
      {count !== undefined ? <span className="tool-catalog-count">{count}</span> : null}
    </div>
    {children}
  </section>;
}

function ToolCard({ tool }: { tool: ToolDefinition }) {
  return (
    <li className="tool-catalog-item">
      <span className="tool-catalog-icon" aria-hidden="true">
        <Icon name={tool.icon} size={16} />
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


type Directory<T> = { items: T[]; error: string; loading: boolean };
const SKILL_TOOLS = new Set(["prepare_skill_creation", "prepare_skill_install", "confirm_skill_install"]);
const fixedTools = TOOL_CATALOG.filter(tool => !SKILL_TOOLS.has(tool.name) && tool.category !== "mcp");
const skillTools = TOOL_CATALOG.filter(tool => SKILL_TOOLS.has(tool.name));
const mcpTools = TOOL_CATALOG.filter(tool => tool.category === "mcp");
const initialDirectory = { items: [], error: "", loading: true };

export function ToolCatalogDialog({ open, userId, status, modelOptions, onClose }: ToolCatalogDialogProps) {
  const [catalog, setCatalog] = useState<{ userId: string; skills: Directory<SkillOption>; mcp: Directory<McpServer> } | null>(null);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setCatalog({ userId, skills: initialDirectory, mcp: initialDirectory });
    const load = async (kind: "skills" | "mcp") => {
      try {
        const result = await (kind === "skills" ? listSkills({ userId }, controller.signal) : listMcp({ userId }, controller.signal));
        if (!controller.signal.aborted) setCatalog(current => current ? { ...current, [kind]: { items: result.items, loading: false, error: "" } } : current);
      } catch {
        if (!controller.signal.aborted) setCatalog(current => current ? { ...current, [kind]: { items: [], loading: false, error: "目录暂时无法加载，请重新打开重试。" } } : current);
      }
    };
    void load("skills");
    void load("mcp");
    return () => controller.abort();
  }, [open, userId]);
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);
  if (!open) return null;
  const skills = catalog?.userId === userId ? catalog.skills : initialDirectory;
  const mcp = catalog?.userId === userId ? catalog.mcp : initialDirectory;
  const availableModels = modelOptions.filter(model => model.available);
  const enabledMcp = mcp.items.filter(server => server.effective_enabled);
  const count = (directory: Directory<unknown>, value: number) => directory.loading ? "加载中" : directory.error ? "加载失败" : value;
  const state = status?.status ?? "starting";
  const stateLabel = { starting: "正在启动助手", ready: "服务已就绪", error: "启动失败" }[state];
  const toolList = (tools: readonly ToolDefinition[]) => <ul className="tool-catalog-list">{tools.map(tool => <ToolCard key={tool.name} tool={tool} />)}</ul>;
  return <div className="tool-catalog-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="tool-catalog-dialog" role="dialog" aria-modal="true" aria-labelledby="tool-catalog-title" onMouseDown={event => event.stopPropagation()}>
      <header className="tool-catalog-header">
        <div><p className="tool-catalog-eyebrow">SYSTEM STATUS</p><h2 id="tool-catalog-title">系统状态</h2><p className="tool-catalog-intro">按模型、工具、技能、MCP 和其他查看当前用户的能力与状态。</p></div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭系统状态"><Icon name="x" size={16} /></button>
      </header>
      <div className="tool-catalog-summary" aria-label="系统状态摘要" aria-live="polite">
        <span className="tool-catalog-summary-state"><span className={`status-dot ${state === "starting" ? "is-loading" : state === "error" ? "is-error" : ""}`} />{stateLabel}</span>
        <span><strong>{availableModels.length}</strong> 个可用模型</span>
        <span><strong>{fixedTools.length}</strong> 个固定工具</span>
        <span><strong>{count(skills, skills.items.length)}</strong> 个启用技能</span>
        <span><strong>{count(mcp, enabledMcp.length)}</strong> 个启用 MCP</span>
      </div>
      <div className="tool-catalog-content">
        <Section id="models" title="模型" icon="brain" description="当前用户已配置的模型；可用状态不代表已完成连接检测。" count={availableModels.length + " 可用"}>
          <ul className="tool-catalog-list">{modelOptions.map(model => <li className="tool-catalog-item" key={model.id}><span className="tool-catalog-item-copy"><strong>{model.display_name}{model.is_default ? " · 默认" : ""}</strong><span className="tool-catalog-item-description">{model.provider} · {model.source === "system" ? "内置模型" : "个人模型"}</span></span><span className={`tool-catalog-badge ${model.available ? "is-runtime" : ""}`}>{model.available ? "可用" : "不可用"}</span></li>)}</ul>
          {!modelOptions.length ? <p className="tool-catalog-empty">尚未配置模型，请先在技能|连接器中配置。</p> : null}
        </Section>
        <Section id="tools" title="工具" icon="wrench" count={fixedTools.length} description="固定能力目录；实际调用受当前模型、客户端与权限配置约束。">
          {TOOL_CATEGORIES.filter(category => category.id !== "mcp").map(category => <div className="tool-catalog-subgroup" key={category.id}><h4>{category.label}<span>{fixedTools.filter(tool => tool.category === category.id).length}</span></h4>{toolList(fixedTools.filter(tool => tool.category === category.id))}</div>)}
        </Section>
        <Section id="skills" title="技能" icon="book-open" count={count(skills, skills.items.length)} description="当前用户已启用的技能，由 Agent 按任务需要读取。">
          {skills.loading || skills.error || !skills.items.length ? <p className="tool-catalog-empty">{skills.loading ? "正在加载技能…" : skills.error || "当前没有启用技能。"}</p> : <ul className="tool-catalog-list">{skills.items.map(skill => <li className="tool-catalog-item" key={skill.id}><span className="tool-catalog-item-copy"><strong>{skill.display_name}</strong><span className="tool-catalog-item-description">{skill.description}</span></span><span className="tool-catalog-badge is-runtime">已启用 · {skill.scope === "global" ? "共享" : "个人"}</span></li>)}</ul>}
          <div className="tool-catalog-subgroup"><h4>技能管理工具<span>{skillTools.length}</span></h4>{toolList(skillTools)}</div>
        </Section>
        <Section id="mcp" title="MCP" icon="plug" count={count(mcp, enabledMcp.length) + " 启用"} description="展示配置状态，不自动检测连接；具体工具在运行时发现并受白名单约束。">
          {mcp.loading || mcp.error || !mcp.items.length ? <p className="tool-catalog-empty">{mcp.loading ? "正在加载 MCP 服务…" : mcp.error || "当前没有配置 MCP 服务。"}</p> : <ul className="tool-catalog-list">{mcp.items.map(server => <li className="tool-catalog-item" key={server.id}><span className="tool-catalog-item-copy"><strong>{server.display_name}</strong><span className="tool-catalog-item-description">{server.slug} · {server.transport} · {server.unavailable_reason || "连接未检测，工具运行时发现"}</span></span><span className={`tool-catalog-badge ${server.effective_enabled ? "is-runtime" : ""}`}>{server.effective_enabled ? "已启用" : "未启用"}</span></li>)}</ul>}
          <div className="tool-catalog-subgroup"><h4>MCP 管理工具<span>{mcpTools.length}</span></h4>{toolList(mcpTools)}</div>
        </Section>
        <Section id="other" title="其他" icon="shield-check" description="服务运行状态与执行边界。">
          <dl className="tool-catalog-status-list"><div><dt>助手服务</dt><dd>{stateLabel}</dd></div><div><dt>执行权限</dt><dd>有副作用的操作遵循审批或受控权限边界。</dd></div><div><dt>工作区隔离</dt><dd>本地 Shell 不是安全沙箱，共享部署需要额外隔离。</dd></div></dl>
        </Section>
      </div>
    </section>
  </div>;
}
