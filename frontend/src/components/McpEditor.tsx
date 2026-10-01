import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Checkbox, Collapse, Input, Modal, Select } from "antd";
import { saveMcp, testMcp } from "../api/mcp";
import type { McpConfiguration, McpCredentialPatch, McpServer, McpTestResult } from "../types/api";
import { importMcp, mcpCandidates, type ImportedMcp } from "../lib/mcpImport";
import { McpCredentials, type CredentialRow } from "./McpCredentials";

interface Draft {
  name: string; slug: string; description: string; transport: ImportedMcp["transport"];
  url: string; command: string; args: string; apiKey: string;
  headers: CredentialRow[]; env: CredentialRow[]; allowlist: string[]; allowMode: "all" | "selected";
}
function initialDraft(server?: McpServer, fixedSlug?: string): Draft {
  return { name: server?.display_name ?? "", slug: server?.slug ?? fixedSlug ?? "", description: server?.description ?? "",
    transport: server?.transport ?? "http", url: server?.url ?? "", command: server?.command ?? "", args: (server?.args ?? []).join("\n"), apiKey: "",
    headers: (server?.headers_keys ?? []).map(key => ({ key, value: "", saved: true, removed: false })),
    env: (server?.env_keys ?? []).map(key => ({ key, value: "", saved: true, removed: false })),
    allowMode: server?.tool_allowlist == null ? "all" : "selected", allowlist: [...(server?.tool_allowlist ?? [])] };
}
function credentialPatch(rows: CredentialRow[], header = false): McpCredentialPatch {
  const patch: McpCredentialPatch = { set: Object.create(null), remove: [], clear: false };
  const names = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    if (!key && !row.value) continue;
    if (!key) throw new Error("请填写凭据名称。");
    if (row.removed) { patch.remove.push(key); continue; }
    const normalized = header ? key.toLowerCase() : key;
    if (names.has(normalized)) throw new Error("凭据名称重复，请合并后重试。");
    names.add(normalized);
    if (!row.saved || row.value) patch.set[key] = row.value;
  }
  return patch;
}
function configuration(draft: Draft): McpConfiguration {
  if (!draft.name.trim() || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(draft.slug)) throw new Error("请填写名称和合法的小写服务标识。");
  if (!draft.transport) throw new Error("请选择 HTTP 或 SSE 连接类型。");
  const stdio = draft.transport === "stdio";
  if (stdio ? !draft.command.trim() : !/^https?:\/\//.test(draft.url.trim())) throw new Error("请填写有效的连接地址或启动程序。");
  const headerRows = [...draft.headers];
  if (draft.apiKey) {
    const index = headerRows.findIndex(row => !row.removed && row.key.toLowerCase() === "authorization");
    if (index >= 0) {
      if (!headerRows[index].saved || headerRows[index].value) throw new Error("API Key 与 Authorization 请求头冲突，请只保留一种配置。");
      headerRows[index] = { ...headerRows[index], value: `Bearer ${draft.apiKey}` };
    } else headerRows.push({ key: "Authorization", value: `Bearer ${draft.apiKey}`, saved: false, removed: false });
  }
  return { slug: draft.slug, display_name: draft.name.trim(), description: draft.description,
    transport: draft.transport, url: stdio ? null : draft.url.trim(), command: stdio ? draft.command.trim() : null,
    args: stdio ? draft.args.split("\n").filter(Boolean) : [],
    headers: stdio ? { set: {}, remove: [], clear: true } : credentialPatch(headerRows, true),
    env: stdio ? credentialPatch(draft.env) : { set: {}, remove: [], clear: true },
    tool_allowlist: draft.allowMode === "all" ? null : [...draft.allowlist] };
}
function importedRows(rows: CredentialRow[], values: Record<string, string>, headers = false): CredentialRow[] {
  const normalize = (key: string) => headers ? key.toLowerCase() : key;
  const saved = rows.filter(row => row.saved).map(row => ({ ...row, value: "", removed: false }));
  for (const [key, value] of Object.entries(values)) {
    const index = saved.findIndex(row => normalize(row.key) === normalize(key));
    if (index >= 0) saved[index] = { ...saved[index], value };
    else saved.push({ key, value, saved: false, removed: false });
  }
  return saved;
}

export function McpEditor({ userId, isAdmin, existing, fixedSlug, onClose, onSaved }: {
  userId: string; isAdmin: boolean; existing?: McpServer; fixedSlug?: string; onClose: () => void; onSaved: () => void;
}) {
  const { modal, message } = App.useApp();
  const [initial] = useState(() => initialDraft(existing, fixedSlug));
  const [draft, setDraft] = useState(initial);
  const [json, setJson] = useState("");
  const [importOpen, setImportOpen] = useState<string[]>([]);
  const [choices, setChoices] = useState<ReturnType<typeof mcpCandidates>>([]);
  const [selected, setSelected] = useState<number | undefined>();
  const [error, setError] = useState("");
  const [result, setResult] = useState<McpTestResult | null>(null);
  const [discoveredTools, setDiscoveredTools] = useState<string[]>([]);
  const [toolListLoaded, setToolListLoaded] = useState(false);
  const [toolQuery, setToolQuery] = useState("");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const request = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const revision = useRef(0);
  const toolSelectionTouched = useRef(initial.allowMode === "selected");
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort(); }; }, []);
  const confirm = (title: string, content: string) => new Promise<boolean>(resolve => modal.confirm({ rootClassName: "mcp-confirm", title, content, okText: "确定", cancelText: "取消", onOk: () => resolve(true), onCancel: () => resolve(false) }));
  const change = (next: Draft, preserveToolList = false) => {
    revision.current++;
    request.current?.abort();
    setTesting(false);
    if (!preserveToolList) setResult(null);
    setError("");
    if (!preserveToolList) { setDiscoveredTools([]); setToolListLoaded(false); }
    setDraft(next);
  };
  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => change({ ...draft, [key]: value }, ["name", "description", "slug"].includes(key));
  const setSelectedTools = (names: readonly string[]) => {
    toolSelectionTouched.current = true;
    change({ ...draft, allowlist: [...names] }, true);
  };
  const changeAllowMode = (allowMode: Draft["allowMode"]) => {
    const allowlist = allowMode === "selected" && !toolSelectionTouched.current && discoveredTools.length
      ? [...discoveredTools]
      : draft.allowlist;
    change({ ...draft, allowMode, allowlist }, true);
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial) || !!json;
  const applyImport = async (candidate: ReturnType<typeof mcpCandidates>[number]) => {
    try {
      const parsed = importMcp(candidate, isAdmin);
      const current = revision.current;
      if ((JSON.stringify(draft) !== JSON.stringify(initial) || existing) && !await confirm("替换表单连接配置？", "将填入新连接。未提供的已保存凭据保留；类型改变时不适用的凭据将清除。")) return;
      if (!alive.current || current !== revision.current) return;
      change({ ...draft, name: parsed.name || draft.name,
        slug: existing || fixedSlug ? draft.slug : /^[a-z0-9][a-z0-9_-]{0,63}$/.test(parsed.name) ? parsed.name : draft.slug,
        transport: parsed.transport, url: parsed.url, command: parsed.command, args: parsed.args.join("\n"), apiKey: "",
        headers: parsed.transport === "stdio" ? [] : importedRows(draft.headers, parsed.headers, true),
        env: parsed.transport === "stdio" ? importedRows(draft.env, parsed.env) : [] });
      setJson(""); setChoices([]); setSelected(undefined); setImportOpen([]);
    } catch (e) { setError(e instanceof Error ? e.message : "解析失败。"); }
  };
  const parse = () => {
    try {
      const candidates = mcpCandidates(json);
      if (candidates.length === 1) void applyImport(candidates[0]);
      else { setChoices(candidates); setSelected(undefined); setError(""); }
    } catch (e) { setError(e instanceof Error ? e.message : "解析失败。"); }
  };
  const runTest = async () => {
    try {
      const currentRevision = revision.current;
      const config = configuration(draft);
      if (existing && config.url !== existing.url && draft.headers.some(row => row.saved && !row.removed) && !await confirm("连接地址已变更", "保留的凭据将发送到新地址。是否继续测试？")) return;
      if (!alive.current || currentRevision !== revision.current) return;
      request.current?.abort();
      const controller = new AbortController(); request.current = controller;
      setTesting(true); setResult(null); setError("");
      try {
        const response = await testMcp(userId, config, existing, controller.signal);
        if (alive.current && !controller.signal.aborted) {
          setResult(response);
          setDiscoveredTools(response.ok ? response.tool_names : []);
          setToolListLoaded(response.ok);
          if (response.ok && draft.allowMode === "selected" && !toolSelectionTouched.current) {
            setDraft(current => ({ ...current, allowlist: [...response.tool_names] }));
          }
        }
      } finally { if (alive.current && request.current === controller) setTesting(false); }
    } catch (e) { if (alive.current && !(e instanceof Error && e.name === "AbortError")) setError(e instanceof Error ? e.message : "测试失败。"); }
  };
  const save = async () => {
    try {
      const currentRevision = revision.current;
      const config = configuration(draft);
      if (existing && config.url !== existing.url && draft.headers.some(row => row.saved && !row.removed) && !await confirm("连接地址已变更", "保存后保留的凭据将发送到新地址。是否继续？")) return;
      if (config.tool_allowlist?.length === 0 && !await confirm("禁止此服务的全部工具？", "白名单为空，保存后此服务不会提供任何工具给 Agent。")) return;
      if (!alive.current || currentRevision !== revision.current) return;
      setSaving(true); request.current?.abort();
      await saveMcp(userId, config, existing);
      if (alive.current) { message.success(existing?.effective_enabled ? "已保存，从下一条消息生效。" : isAdmin ? "已保存，尚未使用；请在卡片上全员启用或添加到我的服务。" : "已保存，尚未使用；请在卡片上添加到我的服务。"); onSaved(); }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "保存失败。"); }
    finally { if (alive.current) setSaving(false); }
  };
  let valid = true;
  try { configuration(draft); } catch { valid = false; }
  return <Modal open title={existing ? "编辑 MCP 服务" : "配置 MCP 服务"} width="min(860px, calc(100vw - 32px))" className="mcp-editor" mask={{ closable: false }}
    onCancel={() => { if (!saving) onClose(); }} footer={
      <div className="mcp-editor-footer">
        <Button disabled={saving || !valid} loading={testing} onClick={() => void runTest()}>测试连接</Button>
        <div className="mcp-editor-footer-actions">
          <Button disabled={saving} onClick={onClose}>取消</Button>
          <Button disabled={saving || !dirty} onClick={async () => { if (await confirm("重置配置？", "未保存的表单、JSON 和新凭据将被清除。")) { toolSelectionTouched.current = initial.allowMode === "selected"; change(initial); setJson(""); setChoices([]); } }}>重置</Button>
          <Button type="primary" disabled={!valid || testing} loading={saving} onClick={() => void save()}>保存</Button>
        </div>
      </div>
    }>
    <p className="resource-hint">{isAdmin ? "保存到系统共享，需全员启用后开放。" : "保存到我的服务，添加后仅自己可用。"} 支持 HTTP、SSE{isAdmin ? "、stdio" : ""}。</p>
    {fixedSlug && <Alert type="warning" showIcon message="保存后同名全局配置将不再使用，请添加个人服务或恢复全局；其他用户不受影响。" />}
    <Collapse activeKey={importOpen} onChange={keys => setImportOpen(typeof keys === "string" ? [keys] : keys)} items={[{ key: "json", label: "从 JSON 导入", children: <>
      <Input.TextArea aria-label="MCP JSON" rows={6} value={json} placeholder={'粘贴 MCP JSON，点击解析后填入下方表单'}
        onChange={e => { setJson(e.target.value); setChoices([]); setSelected(undefined); }} />
      <Button className="mcp-json-import-button" disabled={!json.trim() || saving} onClick={parse}>解析并填充</Button>
      {choices.length > 1 && <div><Select aria-label="选择要导入的服务" placeholder="选择一个服务" value={selected} onChange={setSelected}
        options={choices.map((item, index) => ({ label: item.name, value: index }))} />
        <Button disabled={selected === undefined} onClick={() => { if (selected !== undefined) void applyImport(choices[selected]); }}>填充所选服务</Button>
        <Button onClick={() => { setChoices([]); setSelected(undefined); }}>取消选择</Button></div>}
      {json && <p className="resource-hint">JSON 尚未导入，不参与测试或保存。</p>}
    </> }]} />
    <div className="mcp-fields">
      <label className="mcp-field-half">名称<Input value={draft.name} maxLength={128} onChange={e => update("name", e.target.value)} /></label>
      <label className="mcp-field-half">服务标识<Input value={draft.slug} disabled={!!existing || !!fixedSlug} placeholder="my-mcp-server" onChange={e => update("slug", e.target.value)} /></label>
      <label className="mcp-field-wide">用途说明<Input value={draft.description} onChange={e => update("description", e.target.value)} /></label>
      <label className="mcp-field-transport">连接类型<Select value={draft.transport || undefined} placeholder="请选择连接类型" onChange={async value => {
        if ((draft.env.length || draft.headers.length || draft.apiKey) && (draft.transport === "stdio" || value === "stdio") && !await confirm("切换连接类型？", "不适用的请求头或环境变量将被清除。")) return;
        change({ ...draft, transport: value, ...(value === "stdio" ? { headers: [], apiKey: "", url: "" } : { env: [], command: "", args: "" }) });
      }} options={(isAdmin ? ["http", "sse", "stdio"] : ["http", "sse"]).map(value => ({ value, label: value.toUpperCase() }))} /></label>
      {draft.transport === "stdio" ? <>
        <label className="mcp-field-wide">启动程序<Input value={draft.command} onChange={e => update("command", e.target.value)} /></label>
        <label className="mcp-field-wide">启动参数（每行一个）<Input.TextArea value={draft.args} onChange={e => update("args", e.target.value)} /></label>
        <div className="mcp-field-wide"><McpCredentials label="环境变量" rows={draft.env} onChange={rows => update("env", rows)} /></div>
        <p className="resource-hint mcp-field-wide">测试连接将启动指定程序。凭据请放在环境变量中。</p>
      </> : <>
        <label className="mcp-field-address">服务地址<Input value={draft.url} placeholder="https://example.com/mcp" onChange={e => update("url", e.target.value)} /></label>
        <label className="mcp-field-wide">API Key（可选，作为 Authorization: Bearer 发送）<Input.Password autoComplete="new-password" placeholder={draft.headers.some(row => row.saved && !row.removed && row.key.toLowerCase() === "authorization") ? "已配置；留空保留，输入新 Key 直接替换" : "输入 API Key"} value={draft.apiKey} onChange={e => update("apiKey", e.target.value)} /></label>
        <div className="mcp-field-wide"><McpCredentials label="请求头" rows={draft.headers} onChange={rows => update("headers", rows)} /></div>
      </>}
    </div>
    <Collapse items={[{ key: "advanced", label: "高级配置", children: <>
      <Select aria-label="工具范围" value={draft.allowMode} onChange={changeAllowMode} options={[{ value: "all", label: "允许全部工具" }, { value: "selected", label: "仅允许指定工具" }]} />
      {draft.allowMode === "selected" && <div className="mcp-tool-list">
        <p className="resource-hint">{toolListLoaded ? "按本次目录选择工具；标记为不存在的项可取消。" : testing ? "正在发现工具…" : "已保存的选择仍会保留，测试连接后可核对最新目录。"}</p>
        <Input aria-label="搜索白名单工具" placeholder="搜索工具名称" value={toolQuery} onChange={event => setToolQuery(event.target.value)} />
        <div className="mcp-tool-list-header">
          <span>已选 {draft.allowlist.length}{toolListLoaded ? ` / 发现 ${discoveredTools.length}` : " · 尚未重新验证"}</span>
          <Button type="link" size="small" disabled={!toolListLoaded} onClick={() => setSelectedTools(discoveredTools)}>全选当前目录</Button>
          <Button type="link" size="small" onClick={() => setSelectedTools([])}>清空</Button>
        </div>
        <Checkbox.Group aria-label="MCP 工具白名单" className="mcp-tool-checkboxes" value={draft.allowlist}
          options={[...new Set([...discoveredTools, ...draft.allowlist])]
            .filter(name => name.toLowerCase().includes(toolQuery.toLowerCase()))
            .map(name => ({ label: `${name}${toolListLoaded && !discoveredTools.includes(name) ? "（已不存在）" : ""}`, value: name }))}
          onChange={values => {
            const visible = [...new Set([...discoveredTools, ...draft.allowlist])].filter(name => name.toLowerCase().includes(toolQuery.toLowerCase()));
            setSelectedTools([...new Set([...draft.allowlist.filter(name => !visible.includes(name)), ...values.filter((value): value is string => typeof value === "string")])]);
          }} />
        <p className="resource-hint">不勾选任何工具表示禁止此服务的全部工具，保存前会再次确认。</p>
      </div>}
    </> }]} />
    {error && <Alert role="alert" type="error" title={error} showIcon />}
    {result && <Alert
      role="status"
      type={result.ok ? "success" : "error"}
      title={result.ok ? "连接成功" : result.message}
      description={result.ok && <div className="mcp-test-result">
        <div>工具数量：{result.tool_count}</div>
        {result.tool_names.length ? <ul className="mcp-test-tool-names" aria-label="发现的 MCP 工具">
          {result.tool_names.map(name => <li key={name}>{name}</li>)}
        </ul> : <div>未发现工具名称。</div>}
      </div>}
      showIcon
    />}
  </Modal>;
}
