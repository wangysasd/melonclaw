import { tablePagination } from "./tablePresentation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Space, Switch, Table, Tag, Typography } from "antd";
import { deleteEnvironment, listEnvironment, saveEnvironment, type EnvironmentVariable } from "../api/environment";

import { Icon } from "./Icon";
import { deleteConfirmation } from "./deleteConfirmation";

interface Props { userId: string; isAdmin: boolean }
interface Draft { name: string; description: string; value: string; enabled: boolean }
const emptyDraft: Draft = { name: "", description: "", value: "", enabled: true };

export function EnvironmentManager({ userId, isAdmin }: Props) {
  const { modal } = App.useApp();
  const [rows, setRows] = useState<EnvironmentVariable[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [editor, setEditor] = useState<{ row?: EnvironmentVariable } | null>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm<Draft>();
  const controller = useRef<AbortController | null>(null);
  const acting = useRef(false);
  const reload = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    try {
      const data = await listEnvironment(userId, signal);
      if (!signal.aborted) { setRows(data.items); setError(""); }
    } catch { if (!signal.aborted) { setRows([]); setError("无法读取环境变量，请重试。"); } }
    finally { if (!signal.aborted) setLoading(false); }
  }, [userId]);
  useEffect(() => {
    const current = new AbortController(); controller.current = current;
    void reload(current.signal);
    return () => { current.abort(); form.resetFields(); };
  }, [reload, form]);
  function edit(row?: EnvironmentVariable) {
    setError(""); setEditor({ row });
  }
  function close() { if (!acting.current) { form.resetFields(); setEditor(null); } }
  async function mutate(operation: (signal: AbortSignal) => Promise<unknown>, closeEditor = false) {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted || acting.current) return;
    acting.current = true; setBusy(true); setError("");
    try {
      await operation(signal);
      if (signal.aborted) return;
      if (closeEditor) { form.resetFields(); setEditor(null); }
      await reload(signal);
    } catch (cause) {
      // API 不回显完整值，表单值只保存在当前组件内存中。
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : "保存失败，请重试。");
    } finally { acting.current = false; if (!signal.aborted) setBusy(false); }
  }
  async function save() {
    const draft = await form.validateFields().catch(() => null);
    if (!draft || !editor) return;
    const fields = { description: draft.description, enabled: draft.enabled,
      ...(!editor.row || draft.value !== "" ? { value: draft.value ?? "" } : {}) };
    await mutate(signal => saveEnvironment(userId, draft.name, fields, editor.row, signal), true);
  }
  return <section className="resource-section environment-manager" aria-label="环境变量管理">
    <div className="resource-section">
      {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => { const signal = controller.current?.signal; if (signal) void reload(signal); }}>重试</Button>} />}
      <div className="resource-actions skill-resource-actions"><Input className="skill-search" prefix={<Icon name="search" size={14} />} aria-label="搜索环境变量" placeholder="搜索变量名或说明" value={search} onChange={event => setSearch(event.target.value)} allowClear />
        <div className="skill-actions-trailing"><Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => edit()} disabled={busy}>新增环境变量</Button>
          <Button className="action-secondary" icon={<Icon name="refresh-cw" size={14} />} loading={loading} disabled={busy} aria-label="刷新环境变量列表" onClick={() => { const signal = controller.current?.signal; if (signal) void reload(signal); }} />
        </div></div>
      <Table<EnvironmentVariable> className="environment-table" tableLayout="auto" rowKey="id" loading={loading} scroll={{ x: 850 }} pagination={{ ...tablePagination, pageSize: 20 }}
        dataSource={rows.filter(row => `${row.name} ${row.description}`.toLowerCase().includes(search.toLowerCase()))}
        columns={[
          { title: "启用", render: (_, row) => <Switch aria-label={`${row.name} 启用`} checked={row.enabled} disabled={busy}
            onChange={enabled => void mutate(signal => saveEnvironment(userId, row.name, { enabled }, row, signal))} /> },
          { title: "变量名", dataIndex: "name", render: name => <Typography.Text code>{name}</Typography.Text> },
          { title: "值", dataIndex: "masked_value" },
          { title: "当前来源", dataIndex: "effective_source", render: source => <Tag>{source === "personal" ? "个人" : source === "shared" ? "系统共享" : "未注入"}</Tag> },
          { title: "更新时间", dataIndex: "updated_at", render: date => new Date(date).toLocaleString() },
          { title: "说明", dataIndex: "description", className: "environment-description",
            render: description => <Typography.Text style={{ display: "block", width: "100%" }}
              ellipsis={{ tooltip: description }}>{description}</Typography.Text> },
          { title: "操作", className: "table-actions-cell", fixed: "right", width: 140, render: (_, row) => <Space><Button type="link" disabled={busy} onClick={() => edit(row)}>编辑</Button>
            <Button type="link" danger disabled={busy} onClick={() => modal.confirm({
              ...deleteConfirmation({
                title: "删除环境变量？", name: row.name,
                metadata: row.scope === "global" ? "系统共享" : "个人变量", icon: <Icon name="terminal" size={24} />,
                description: isAdmin ? "依赖此共享变量的用户将不再获得该值。" : "删除后将自动使用可用的同名系统共享值。",
              }),
              onOk: () => mutate(signal => deleteEnvironment(userId, row, signal)),
            })}>删除</Button></Space> },
        ]} />
    </div>
    <Modal title={editor?.row ? "编辑环境变量" : "新增环境变量"} open={editor !== null} onCancel={close}
      onOk={() => void save()} confirmLoading={busy} cancelButtonProps={{ disabled: busy }} destroyOnHidden>
      {editor && <Form form={form} initialValues={{ ...emptyDraft, ...(editor.row ? { name: editor.row.name, description: editor.row.description, enabled: editor.row.enabled } : {}) }} layout="vertical" preserve={false} autoComplete="off">
        <Form.Item name="name" label="变量名" rules={[{ required: true, pattern: /^[A-Za-z_][A-Za-z0-9_]{0,127}$/, message: "以字母或下划线开头，仅含字母、数字和下划线，最多 128 位" }]}>
          <Input disabled={!!editor?.row} maxLength={128} /></Form.Item>
        <Form.Item name="value" label={editor.row ? "值" : "值（空字符串也会保存）"}>
          <Input.Password autoComplete="new-password" maxLength={8192} visibilityToggle={false}
            placeholder={editor.row ? "已配置（不回显）；留空保留，输入新值并保存即可更新" : "输入变量值（只保存不回显）"} /></Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={500} /></Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        {error && <Alert type="error" message={error} showIcon />}
      </Form>}
    </Modal>
  </section>;
}
