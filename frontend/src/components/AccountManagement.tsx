import { tablePagination } from "./tablePresentation";
import { PasswordFields } from "./PasswordFields";
import { Icon } from "./Icon";
import { tokens } from "../theme/tokens";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { App, Button, Form, Input, Modal, Select, Space, Switch, Table, Tabs, Tag } from "antd";
import { useEffect, useRef, useState } from "react";
import { apiRequest } from "../api/client";
import { notifyAuthChange } from "../api/auth";
import { useSession } from "../state/session";

type Tenant = { tenant_id: string; tenant_name_zh: string; enabled: boolean; created_at: string };
type User = { user_id: string; user_name_zh: string; tenant_id: string; tenant_name_zh: string; tenant_enabled: boolean; created_at: string };
type Editor = { kind: "user"; row?: User } | { kind: "tenant"; row?: Tenant } | { kind: "password"; row: User };
type Values = { user_id: string; user_name_zh: string; tenant_id: string; tenant_name_zh: string; enabled: boolean; password: string; confirm_password: string };

export function AccountManagement({ onClose }: { onClose: () => void }) {
  const session = useSession();
  const { message, modal } = App.useApp();
  const [users, setUsers] = useState<User[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tab, setTab] = useState("users");
  const [search, setSearch] = useState("");
  const [tenant, setTenant] = useState<string>();
  const [editor, setEditor] = useState<Editor | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form] = Form.useForm<Values>();
  const refreshSequence = useRef(0);
  async function refresh() {
    const sequence = ++refreshSequence.current;
    setLoading(true);
    try {
      const [u, t] = await Promise.all([
        apiRequest<{ items: User[] }>("/api/admin/users"),
        apiRequest<{ items: Tenant[] }>("/api/admin/tenants"),
      ]);
      if (sequence !== refreshSequence.current) return;
      setUsers(u.items); setTenants(t.items);
    } catch (err) {
      if (sequence === refreshSequence.current) message.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (sequence === refreshSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    void refresh();
    return () => { refreshSequence.current += 1; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  function open(next: Editor) {
    form.resetFields();
    form.setFieldsValue(next.kind === "password" ? {} : next.row ?? { enabled: true });
    setEditor(next);
  }
  async function save(values: Values) {
    if (!editor) return;
    setSaving(true);
    try {
      if (editor.kind === "password") {
        await apiRequest(`/api/admin/users/${encodeURIComponent(editor.row.user_id)}/password`, { method: "POST", body: { password: values.password, confirm_password: values.confirm_password } });
      } else if (editor.kind === "user") {
        const body = { user_name_zh: values.user_name_zh, tenant_id: values.tenant_id };
        await apiRequest(editor.row ? `/api/admin/users/${encodeURIComponent(editor.row.user_id)}` : "/api/admin/users", {
          method: editor.row ? "PATCH" : "POST",
          body: editor.row ? body : { ...body, user_id: values.user_id, password: values.password, confirm_password: values.confirm_password },
        });
      } else {
        const body = { tenant_name_zh: values.tenant_name_zh, enabled: values.enabled };
        await apiRequest(editor.row ? `/api/admin/tenants/${encodeURIComponent(editor.row.tenant_id)}` : "/api/admin/tenants", {
          method: editor.row ? "PATCH" : "POST",
          body: editor.row ? body : { ...body, tenant_id: values.tenant_id },
        });
      }
      message.success("已保存"); setEditor(null); form.resetFields();
      if (editor.kind === "password" && editor.row.user_id === session.userId) { notifyAuthChange(); return; }
      await Promise.all([refresh(), session.refreshUsers()]);
    } catch (err) { message.error(err instanceof Error ? err.message : "保存失败"); }
    finally { setSaving(false); }
  }
  function remove(user: User) {
    modal.confirm({
      rootClassName: "delete-confirm-modal", centered: true, width: 440, icon: null,
      styles: { container: { padding: tokens["space-6"] }, body: { padding: 0 } },
      title: "删除用户？",
      content: <div className="delete-confirm-content">
        <div className="delete-confirm-resource">
          <Icon name="users" size={24} />
          <div><strong>{user.user_name_zh}</strong><span>用户 ID：{user.user_id}</span></div>
        </div>
        <p>删除此用户后，个人数据将保留。</p>
      </div>,
      okText: "确认删除", cancelText: "取消", focusable: { autoFocusButton: "cancel" }, okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiRequest(`/api/admin/users/${encodeURIComponent(user.user_id)}`, { method: "DELETE" });
          await Promise.all([refresh(), session.refreshUsers()]); message.success("用户已删除");
        } catch (err) { message.error(err instanceof Error ? err.message : "删除失败"); throw err; }
      },
    });
  }
  const keyword = search.trim().toLowerCase();
  const matches = (...values: string[]) => values.some((value) => value.toLowerCase().includes(keyword));
  const idRules = [{ required: true, pattern: /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/, message: "以字母或数字开头，1～64 位字母、数字、下划线或连字符" }];
  return <section className="account-page">
    <WorkspaceHeader title="系统管理" icon="users" actions={<Button className="action-secondary" icon={<Icon name="arrow-up" size={16} rotate={-90} />} onClick={onClose}>返回聊天</Button>} />
    <div className="account-page-content">
    <Tabs activeKey={tab} onChange={(value) => { setTab(value); setSearch(""); }} items={[{ key: "users", label: <span className="resource-tab-label"><Icon name="users" size={18} />用户管理</span> }, { key: "tenants", label: <span className="resource-tab-label"><Icon name="building-2" size={18} />租户管理</span> }]} />
    <div className="account-toolbar resource-actions skill-resource-actions">
      <Input className="skill-search" prefix={<Icon name="search" size={14} />} allowClear aria-label="搜索 ID 或名称" placeholder="搜索 ID 或名称" value={search} onChange={(event) => setSearch(event.target.value)} />
      {tab === "users" ? <Select style={{ width: 140 }} allowClear placeholder="全部租户" aria-label="筛选租户" value={tenant} onChange={setTenant} options={tenants.map((t) => ({ value: t.tenant_id, label: `${t.tenant_name_zh}（${t.tenant_id}）` }))} /> : null}
      <div className="skill-actions-trailing"><Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => open({ kind: tab === "users" ? "user" : "tenant" })}>{tab === "users" ? "创建用户" : "创建租户"}</Button>
      <Button className="action-secondary" icon={<Icon name="refresh-cw" size={14} />} aria-label={tab === "users" ? "刷新用户列表" : "刷新租户列表"} onClick={() => void refresh()} loading={loading} /></div>
    </div>
    {tab === "users" ? <Table<User> rowKey="user_id" loading={loading} scroll={{ x: 800 }} pagination={{ ...tablePagination, pageSize: 10, showSizeChanger: false }}
      dataSource={users.filter((u) => matches(u.user_id, u.user_name_zh) && (!tenant || tenant === u.tenant_id))}
      columns={[
        { title: "用户 ID", dataIndex: "user_id" }, { title: "用户名称", dataIndex: "user_name_zh" },
        { title: "所属租户", render: (_, u) => <>{u.tenant_name_zh}（{u.tenant_id}）{!u.tenant_enabled ? <Tag>已停用</Tag> : null}</> },
        { title: "创建时间", dataIndex: "created_at", render: (v: string) => new Date(v).toLocaleString() },
        { title: "操作", className: "table-actions-cell", fixed: "right", width: 240, render: (_, u) => <Space><Button type="link" onClick={() => open({ kind: "user", row: u })}>编辑</Button><Button type="link" onClick={() => open({ kind: "password", row: u })}>修改密码</Button><Button type="link" danger disabled={u.user_id === "admin"} onClick={() => remove(u)}>删除</Button></Space> },
      ]} /> : <Table<Tenant> rowKey="tenant_id" loading={loading} scroll={{ x: 600 }} pagination={{ ...tablePagination, pageSize: 10, showSizeChanger: false }}
      dataSource={tenants.filter((t) => matches(t.tenant_id, t.tenant_name_zh))}
      columns={[
        { title: "租户 ID", dataIndex: "tenant_id" }, { title: "租户名称", dataIndex: "tenant_name_zh" },
        { title: "状态", render: (_, t) => <Tag color={t.enabled ? "green" : "default"}>{t.enabled ? "启用" : "停用"}</Tag> },
        { title: "创建时间", dataIndex: "created_at", render: (v: string) => new Date(v).toLocaleString() },
        { title: "操作", className: "table-actions-cell", fixed: "right", width: 90, render: (_, t) => <Button type="link" onClick={() => open({ kind: "tenant", row: t })}>编辑</Button> },
      ]} />}
    </div>
    <Modal destroyOnHidden title={editor?.kind === "password" ? `修改密码 · ${editor.row.user_name_zh}` : `${editor?.row ? "编辑" : "创建"}${editor?.kind === "user" ? "用户" : "租户"}`} open={editor !== null} onCancel={() => { if (!saving) { setEditor(null); form.resetFields(); } }} onOk={() => form.submit()} confirmLoading={saving} okText="保存" cancelText="取消">
      <Form form={form} layout="vertical" onFinish={(values) => void save(values)} requiredMark={false}>
        {editor?.kind === "user" ? <>
          <Form.Item name="user_id" label="用户 ID" rules={idRules}><Input disabled={!!editor.row} maxLength={64} /></Form.Item>
          <Form.Item name="user_name_zh" label="用户名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="tenant_id" label="所属租户" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" disabled={editor.row?.user_id === "admin"} options={tenants.filter((t) => t.enabled || t.tenant_id === editor.row?.tenant_id).map((t) => ({ value: t.tenant_id, label: `${t.tenant_name_zh}（${t.tenant_id}）`, disabled: !t.enabled }))} /></Form.Item>
        </> : null}
        {editor?.kind === "password" || (editor?.kind === "user" && !editor.row) ? <PasswordFields label={editor.kind === "password" ? "新密码" : "初始密码"} /> : null}
        {editor?.kind === "tenant" ? <>
          <Form.Item name="tenant_id" label="租户 ID" rules={idRules}><Input disabled={!!editor.row} maxLength={64} /></Form.Item>
          <Form.Item name="tenant_name_zh" label="租户名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked" extra="停用后，租户成员不能登录或发起业务操作，历史数据保留。"><Switch disabled={editor.row?.tenant_id === "system"} /></Form.Item>
        </> : null}
      </Form>
    </Modal>
  </section>;
}
