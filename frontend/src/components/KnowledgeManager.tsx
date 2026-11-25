import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Checkbox, Drawer, Dropdown, Empty, Form, Input, InputNumber, Modal, Radio, Select, Space, Table, Tabs, Tag, Tooltip } from "antd";
import { knowledgeApi, roleNames, shareScopeNames, type KnowledgeBase, type KnowledgeCandidate, type KnowledgeMember, type KnowledgeShareScope } from "../api/knowledge";
import { useSession } from "../state/session";
import { KnowledgeSearchTest } from "./KnowledgeSearchTest";
import { KnowledgeDocuments } from "./KnowledgeDocuments";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { Icon } from "./Icon";
import { confirmModal, deleteConfirmation } from "./deleteConfirmation";
import "../styles/knowledge.css";


export function KnowledgeManager({ onClose }: { onClose: () => void }) {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<KnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<KnowledgeBase | null>(null);
  const [detailTab, setDetailTab] = useState("documents");
  const [editing, setEditing] = useState<KnowledgeBase | "new" | null>(null);
  const [memberBase, setMemberBase] = useState<KnowledgeBase | null>(null);
  const [saving, setSaving] = useState(false);
  const [editorCandidates, setEditorCandidates] = useState<KnowledgeCandidate[]>([]);
  const [privilegedMembers, setPrivilegedMembers] = useState<string[]>([]);
  const [candidateError, setCandidateError] = useState("");
  const [form] = Form.useForm();
  const editorScope = Form.useWatch("share_scope", form);
  const selectedUsers = Form.useWatch("selected_user_ids", form) as string[] | undefined;
  const refresh = useCallback(() => setRevision((v) => v + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void knowledgeApi.list(page, search, controller.signal).then((r) => { if (!controller.signal.aborted) { setRows(r.items); setTotal(r.total); setError(""); } }).catch((e: unknown) => {
      if (!controller.signal.aborted) { setRows([]); setError(e instanceof Error ? e.message : "读取失败"); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, search, revision]);

  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); refresh(); } catch (e) { message.error(e instanceof Error ? e.message : "操作失败"); }
  };
  const openDocuments = (id: string) => run(async () => {
    setDetail(await knowledgeApi.detail(id));
    setDetailTab("documents");
  });
  const openEditor = async (base: KnowledgeBase | "new") => {
    try {
      const current = base === "new" ? null : await knowledgeApi.detail(base.id);
      const members = current ? (await knowledgeApi.members(current.id)).items : [];
      form.setFieldsValue(current ? { ...current, ...current.chunk_config, selected_user_ids: members.filter((m) => m.role === "viewer").map((m) => m.user_id) } : { name: "", description: "", knowledge_mode: "vector", share_scope: "tenant", selected_user_ids: [], chunk_size: 800, chunk_overlap: 120 });
      setEditorCandidates(members); setPrivilegedMembers(members.filter((m) => m.role !== "viewer").map((m) => m.user_id)); setCandidateError("");
      setEditing(current ?? "new");
    } catch (e) { message.error(e instanceof Error ? e.message : "读取配置失败"); }
  };
  useEffect(() => {
    if (!editing || editorScope !== "selected") return;
    let active = true;
    void knowledgeApi.userCandidates().then((result) => { if (active) setEditorCandidates((previous) => [...previous.filter((p) => !result.items.some((item) => item.user_id === p.user_id)), ...result.items]); }).catch(() => { if (active) setCandidateError("用户列表读取失败，请重试。") });
    return () => { active = false; };
  }, [editing, editorScope]);
  const save = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    if (editing !== "new" && editing && editing.share_scope !== "selected" && values.share_scope === "selected") {
      const confirmed = await new Promise<boolean>((resolve) => modal.confirm({ ...scopeConfirmation(), onOk: () => resolve(true), onCancel: () => resolve(false) }));
      if (!confirmed) return;
    }
    setSaving(true);
    try {
      const body = { name: values.name, description: values.description, share_scope: values.share_scope, ...(values.share_scope === "selected" ? { selected_user_ids: values.selected_user_ids || [] } : {}), chunk_config: { chunk_size: values.chunk_size, chunk_overlap: values.chunk_overlap } };
      const creating = body;
      const result = editing === "new" ? await knowledgeApi.create(creating) : await knowledgeApi.update((editing as KnowledgeBase).id, { ...body, version: (editing as KnowledgeBase).version });
      setEditing(null); if (detail?.id === result.id) setDetail(result); refresh(); message.success("知识库已保存");
    } catch (e) { message.error(e instanceof Error ? e.message : "保存失败"); } finally { setSaving(false); }
  };
  const confirmDelete = (base: KnowledgeBase) => modal.confirm({
    ...deleteConfirmation({
      title: "删除知识库？",
      name: base.name,
      metadata: "知识库",
      icon: <Icon name="knowledge-books" size={20} />,
      description: "删除后立即停止访问并取消处理作业，保留审计记录。",
    }),
    onOk: async () => {
      try {
        await knowledgeApi.delete(base.id, base.version);
        setDetail((current) => current?.id === base.id ? null : current);
        refresh();
      } catch (e) {
        message.error(e instanceof Error ? e.message : "删除失败");
        throw e;
      }
    },
  });
  return <main className="resource-view knowledge-view" aria-label="知识库">
    <WorkspaceHeader title="知识库" icon="knowledge-books" actions={<Button className="action-secondary" icon={<Icon name="arrow-up" size={16} rotate={-90} />} onClick={onClose}>返回聊天</Button>} />
    <section className="knowledge-content">
        <div className="skill-resource-actions">
          <Input className="skill-search" allowClear aria-label="搜索知识库" placeholder="搜索知识库" prefix={<Icon name="search" size={14} />} value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
          <Space><Button type="primary" onClick={() => void openEditor("new")}>创建知识库</Button><Button aria-label="刷新知识库" icon={<Icon name="refresh-cw" size={16} />} onClick={refresh} /></Space>
        </div>
        {error ? <Alert type="error" showIcon title={error} /> : null}
        <Table tableLayout="fixed" rowKey="id" loading={loading} dataSource={rows} scroll={{ x: 1000 }} pagination={{ current: page, total, pageSize: 20, showSizeChanger: false, onChange: setPage }} locale={{ emptyText: <Empty description="暂无可访问的知识库" /> }} columns={[
          { title: "知识库", dataIndex: "name", width: "calc((100% - 752px) * 0.7)", render: (_, base) => <Button type="link" onClick={() => void openDocuments(base.id)}>{base.name}</Button> },
          { title: "描述", dataIndex: "description", ellipsis: { showTitle: false }, render: (description: string) => description ? <Tooltip title={description} placement="topLeft" styles={{ container: { fontSize: "var(--text-md)", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } }}><span className="knowledge-description">{description}</span></Tooltip> : null },
          { title: "共享", dataIndex: "share_scope", width: 88, render: (value: KnowledgeShareScope) => <Tag className="knowledge-meta-tag">{shareScopeNames[value]}</Tag> },
          { title: "权限", dataIndex: "role", width: 104, render: (role: KnowledgeBase["role"]) => <Tag className="knowledge-meta-tag">{roleNames[role]}</Tag> },
          { title: "操作", width: 280, className: "knowledge-actions-cell", render: (_, base) => {
            const canManage = base.role === "owner" || base.role === "system_admin";
            return <Space>
              {base.role !== "viewer" ? <Button className="action-secondary" onClick={() => void openDocuments(base.id)}>上传</Button> : null}
              {canManage ? <>
                <Button className="action-secondary" onClick={() => void openEditor(base)}>编辑</Button>
                <Button className="action-secondary" onClick={() => void run(async () => setMemberBase(await knowledgeApi.detail(base.id)))}>成员</Button>
                <Dropdown trigger={["click"]} menu={{
                  items: [{ key: "delete", label: "删除知识库", danger: true }],
                  onClick: () => confirmDelete(base),
                }}>
                  <Button className="action-secondary" aria-label="更多操作" aria-haspopup="menu" title="更多操作">…</Button>
                </Dropdown>
              </> : null}
            </Space>;
          } },
        ]} />
    </section>
    <Modal forceRender width={728} className="knowledge-editor-modal" title={editing === "new" ? "创建知识库" : "编辑知识库"} open={editing !== null} onCancel={() => setEditing(null)} onOk={() => void save()} confirmLoading={saving} okText="保存" cancelText="取消">
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, max: 255 }]}><Input maxLength={255} /></Form.Item>
        <Form.Item name="description" label="描述"><Input.TextArea rows={3} maxLength={4000} /></Form.Item>
        <Form.Item name="share_scope" label="查看共享范围" rules={[{ required: true }]}><ShareScopeCards /></Form.Item>
        {editorScope === "selected" && <Form.Item name="selected_user_ids" label="指定查看人员" extra="在此选择可以查看的人员。已有编辑者仍可访问，无需在此选择。">
          <Select mode="multiple" maxCount={100} aria-label="指定查看人员" placeholder="搜索并选择用户" showSearch filterOption={false} options={editorCandidates.filter((item) => item.user_id !== (editing !== "new" ? editing?.owner_user_id : undefined) && !privilegedMembers.includes(item.user_id)).map((item) => ({ value: item.user_id, label: userLabel(item) }))} optionRender={(option) => <><Checkbox checked={selectedUsers?.includes(String(option.value))} tabIndex={-1} style={{ pointerEvents: "none", marginRight: 8 }} />{option.label}</>} onSearch={(value) => { void knowledgeApi.userCandidates(value).then((result) => { setEditorCandidates((previous) => [...previous.filter((p) => !result.items.some((item) => item.user_id === p.user_id)), ...result.items]); setCandidateError(""); }).catch(() => setCandidateError("用户列表读取失败，请重试。")); }} />
        </Form.Item>}
        {editorScope === "selected" && candidateError ? <Alert type="error" title={candidateError} /> : null}
        <Form.Item name="chunk_size" label="分块大小（字符）" extra="最多 2000 字，保存值就是实际切分长度。" rules={[{ required: true }]}><InputNumber min={100} max={2000} precision={0} /></Form.Item>
        <Form.Item name="chunk_overlap" label="重叠字符" extra="不超过分块大小的 20%。" dependencies={["chunk_size"]} rules={[{ required: true }, ({ getFieldValue }) => ({ validator: (_, value) => Number(value) <= Math.floor(Number(getFieldValue("chunk_size")) / 5) ? Promise.resolve() : Promise.reject(new Error("重叠不能超过分块大小的 20%")) })]}><InputNumber min={0} max={400} precision={0} /></Form.Item>
      </Form>
    </Modal>
    <Drawer title={detail?.name ?? "知识库详情"} open={detail !== null} onClose={() => setDetail(null)} size="large">
      {detail ? <>
        <div className="knowledge-detail-summary">
          <span>共享状态 <Tag className="knowledge-meta-tag">{{ everyone: "所有人", tenant: "同租户", selected: "指定人员" }[detail.share_scope]}</Tag></span>
          <span>权限 <Tag className="knowledge-meta-tag">{roleNames[detail.role]}</Tag></span>
        </div>
        <Tabs activeKey={detailTab} onChange={setDetailTab} key={detail.id} destroyOnHidden items={[
          { key: "documents", label: "文档", children: <KnowledgeDocuments base={detail} /> },
          { key: "search", label: "检索测试", children: <KnowledgeSearchTest base={detail} /> },
          { key: "info", label: "库信息", children: <dl className="knowledge-detail-info">
            <dt>描述</dt><dd>{detail.description || "暂无描述"}</dd>
            <dt>所有者</dt><dd>{detail.owner_user_id}</dd>
            {detail.chunk_config ? <>
              <dt>分块大小</dt><dd>{detail.chunk_config.chunk_size} 字符</dd>
              <dt>重叠字符</dt><dd>{detail.chunk_config.chunk_overlap} 字符</dd>
            </> : null}
          </dl> },
        ]} />
      </> : null}
    </Drawer>
    {memberBase ? <KnowledgeMembers key={memberBase.id} base={memberBase} onScopeSaved={(updated) => { setDetail((current) => current?.id === updated.id ? updated : current); refresh(); }} onClose={() => setMemberBase(null)} onChanged={() => { setMemberBase(null); setDetail(null); refresh(); }} /> : null}
  </main>;
}

const shareScopeHelp: Record<KnowledgeShareScope, string> = {
  everyone: "所有有效用户都可以查看和下载原件。",
  tenant: "与所有者当前租户相同的用户可以查看和下载。所有者更换后，范围跟着新所有者。",
  selected: "所有者和指定成员可以访问。",
};

function ShareScopeCards({ value, onChange, disabled }: { value?: KnowledgeShareScope; onChange?: (value: KnowledgeShareScope) => void; disabled?: boolean }) {
  return <Radio.Group aria-label="查看共享范围" className="knowledge-scope-cards" value={value} disabled={disabled} onChange={(event) => onChange?.(event.target.value)}>
    {(["everyone", "tenant", "selected"] as const).map((scope) => <Radio key={scope} value={scope} aria-label={shareScopeNames[scope]} className="knowledge-scope-card"><span className="knowledge-scope-title">{shareScopeNames[scope]}</span><span className="knowledge-scope-description">{shareScopeHelp[scope]}</span></Radio>)}
  </Radio.Group>;
}

function scopeConfirmation() {
  return confirmModal({ title: "改为只允许指定的人访问？", content: "切换后，所有者、成员列表中的人员和系统管理员可以访问；其他人将失去访问权限。", okText: "确认切换" });
}

function userLabel(user: { user_name_zh: string; user_id: string; tenant_name_zh: string }) {
  return `${user.user_name_zh}（${user.user_id}）· ${user.tenant_name_zh}`;
}

function KnowledgeMembers({ base, onClose, onChanged, onScopeSaved }: { base: KnowledgeBase; onScopeSaved: (updated: KnowledgeBase) => void; onClose: () => void; onChanged: () => void }) {
  const { message, modal } = App.useApp();
  const session = useSession();
  const [members, setMembers] = useState<KnowledgeMember[]>([]);
  const [candidates, setCandidates] = useState<KnowledgeCandidate[]>([]);
  const [target, setTarget] = useState<string>();
  const [role, setRole] = useState("viewer");
  const [scope, setScope] = useState<KnowledgeShareScope>(base.share_scope);
  const [draftScope, setDraftScope] = useState(scope);
  const [version, setVersion] = useState(base.version);
  const [transferTarget, setTransferTarget] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    const [m, c] = await Promise.all([knowledgeApi.members(base.id), knowledgeApi.candidates(base.id)]);
    setMembers(m.items); setCandidates(c.items); setError("");
  }, [base.id]);
  useEffect(() => { void reload().catch((e: unknown) => setError(e instanceof Error ? e.message : "成员读取失败")); }, [reload]);
  const action = async (fn: () => Promise<unknown>) => {
    setBusy(true); try { await fn(); await reload(); } catch (e) { message.error(e instanceof Error ? e.message : "操作失败"); } finally { setBusy(false); }
  };
  const saveScope = async () => {
    setBusy(true);
    try {
      const updated = await knowledgeApi.setShareScope(base.id, draftScope, version);
      setScope(updated.share_scope); setDraftScope(updated.share_scope); setVersion(updated.version);
      onScopeSaved(updated);
      message.success("默认查看范围已保存");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "保存范围失败");
    } finally {
      setBusy(false);
    }
  };
  const confirmScope = () => {
    if (draftScope === "selected") {
      modal.confirm({ ...scopeConfirmation(), onOk: saveScope });
    } else {
      void saveScope();
    }
  };
  const roles = (["viewer", "editor"] as const).map((value) => ({ value, label: roleNames[value] }));
  const candidateOptions = candidates.map((c) => ({ value: c.user_id, label: userLabel(c) }));
  return <Drawer title={`成员管理 · ${base.name}`} open onClose={onClose} size="large">
    <div className="knowledge-members">
      <p className="knowledge-owner">所有者：<strong>{base.owner_user_id}</strong></p>
      {error ? <Alert type="error" title={error} /> : null}
      <section className="knowledge-member-section" aria-labelledby="knowledge-default-access">
        <h3 id="knowledge-default-access">默认查看范围</h3>
        <p>决定谁无需单独授权就能查看和下载原件。系统管理员始终可以访问。</p>
        <ShareScopeCards value={draftScope} disabled={busy} onChange={setDraftScope} />
        <div className="knowledge-scope-footer">
          <span role="status">当前生效：{shareScopeNames[scope]}{draftScope !== scope ? " · 有未保存的更改" : ""}</span>
          <Button type="primary" disabled={draftScope === scope} loading={busy && draftScope !== scope} onClick={confirmScope}>保存范围</Button>
        </div>
      </section>
      <section className="knowledge-member-section" aria-labelledby="knowledge-explicit-access">
      <h3 id="knowledge-explicit-access">单独授权</h3>
      <p>下表只列出单独授权的成员。可以给范围外的人开放访问，也可以为成员授予编辑权限。编辑者可以处理全库文档。</p>
      <Space wrap className="knowledge-member-toolbar">
        <Select aria-label="选择成员" showSearch optionFilterProp="label" placeholder="搜索并选择用户" disabled={busy} options={candidateOptions.filter((c) => !members.some((m) => m.user_id === c.value))} value={target} onChange={setTarget} onSearch={(q) => void knowledgeApi.candidates(base.id, q).then((r) => setCandidates(r.items)).catch(() => setError("成员搜索失败"))} />
        <Select aria-label="授权角色" disabled={busy} options={roles} value={role} onChange={setRole} />
        <Button type="primary" loading={busy} disabled={!target} onClick={() => void action(async () => { await knowledgeApi.saveMember(base.id, target!, role); setTarget(undefined); setRole("viewer"); message.success("成员已授权"); })}>授权</Button>
      </Space>
      <Table rowKey="user_id" dataSource={members} pagination={false} scroll={{ x: 420 }} locale={{ emptyText: "暂无单独授权的成员" }} columns={[
        { title: "成员", dataIndex: "user_name_zh" },
        { title: "租户", dataIndex: "tenant_name_zh" },
        { title: "角色", render: (_, m) => <Select aria-label={`${m.user_name_zh}的角色`} value={m.role} options={roles} disabled={busy} onChange={(v) => void action(() => knowledgeApi.saveMember(base.id, m.user_id, v))} /> },
        { title: "操作", render: (_, m) => <Button danger disabled={busy} onClick={() => modal.confirm({ ...deleteConfirmation({ title: "撤销成员权限？", name: m.user_name_zh, metadata: roleNames[m.role], icon: <Icon name="users" size={20} />, description: "只移除此人的单独授权。如果默认查看范围仍覆盖此人，或此人是系统管理员，仍可访问。历史对话和已下载的内容无法追回。" }), okText: "确认撤权", onOk: () => action(() => knowledgeApi.revoke(base.id, m.user_id)) })}>撤权</Button> },
      ]} />
      </section>
      {base.role === "owner" ? <details className="knowledge-member-section"><summary>所有权设置</summary><p>可转让给任意有效用户。转让后你将成为编辑者。</p><Space wrap><Select aria-label="新所有者" placeholder="选择新所有者" disabled={busy} showSearch optionFilterProp="label" options={candidateOptions.filter((c) => c.value !== session.userId)} value={transferTarget} onChange={setTransferTarget} style={{ width: "min(240px, 65vw)" }} /><Button disabled={!transferTarget || busy} onClick={() => modal.confirm({ ...confirmModal({ title: "转让知识库所有权？", content: "转让后，你将成为编辑者，并交出知识库设置和成员授权的管理权。如果默认查看范围是同租户，范围将随新所有者的租户变化。", okText: "确认转让" }), onOk: async () => { try { await knowledgeApi.transfer(base.id, transferTarget!, version); onChanged(); } catch (e) { message.error(e instanceof Error ? e.message : "转让失败"); throw e; } } })}>转让所有权</Button></Space></details> : null}
    </div>
  </Drawer>;
}
