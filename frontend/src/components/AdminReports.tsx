import { Alert, Button, Drawer, Form, Input, Select, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import "../styles/reports.css";
import {
  acceptReport,
  getAdminReport,
  listAdminReports,
  noteReport,
  readReportedMessage,
  replyReport,
  reportEventName,
  reportReasons,
  reportStatusNames,
  resolveReport,
  type AdminReportDetail,
  type AdminReportSummary,
  type ReportStatus,
} from "../api/reports";

const statusColor = { pending: "gold", processing: "blue", resolved: "green" } as const;

function liveText(detail: AdminReportDetail) {
  const live = detail.target.live;
  if (!live) return "";
  if (!live.available) return "原内容已不在库中。以下摘要是举报时留存的内容。";
  if (live.superseded) return "文档已有更新版本。工单仍指向举报时的版本，以下是当时留存的摘要。";
  if (!live.hash_matches) return "库中内容与举报时的摘要不一致。以下是举报时留存的摘要。";
  return "库中的该版本与举报时一致。";
}

function ReportedMessage({ id }: { id: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  return content === null ? <>
    <Button loading={loading} onClick={() => {
      setLoading(true);
      setError("");
      void readReportedMessage(id).then((result) => setContent(result.content)).catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "读取失败");
      }).finally(() => setLoading(false));
    }}>查看被举报的回复</Button>
    {error ? <Alert type="error" title={error} showIcon /> : null}
  </> : <p className="reported-message-content">{content}</p>;
}

export function AdminReports() {
  const [items, setItems] = useState<AdminReportSummary[]>([]);
  const [status, setStatus] = useState<ReportStatus | "">("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<AdminReportDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  async function refresh(nextStatus = status, nextCode = code) {
    setLoading(true);
    try {
      const result = await listAdminReports({ status: nextStatus || undefined, code: nextCode.trim() || undefined });
      setItems(result.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function open(id: string) {
    setError("");
    try {
      const next = await getAdminReport(id);
      setDetail(next);
      form.setFieldsValue({ note: "", feedback: next.public_feedback ?? "", measure: next.measure ?? "" });
    } catch (err) { setError(err instanceof Error ? err.message : "加载失败"); }
  }
  async function run(action: () => Promise<AdminReportDetail>) {
    setBusy(true); setError("");
    try {
      const next = await action();
      setDetail(next);
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "操作失败"); }
    finally { setBusy(false); }
  }
  const target = detail?.target;
  return <div className="report-page">
    {error && !detail ? <Alert type="error" title={error} showIcon style={{ marginBottom: 12 }} /> : null}
    <div className="account-toolbar">
      <Input.Search aria-label="按工单编号查询" placeholder="按工单编号查询" value={code} maxLength={32} onChange={(event) => setCode(event.target.value)} onSearch={(value) => void refresh(status, value)} enterButton="查询" style={{ maxWidth: 320 }} />
      <Select aria-label="按状态筛选" virtual={false} value={status} style={{ width: 140 }} onChange={(value) => { setStatus(value); void refresh(value, code); }}
        options={[{ value: "", label: "全部状态" }, { value: "pending", label: "待受理" }, { value: "processing", label: "处理中" }, { value: "resolved", label: "已办结" }]} />
      <Button className="action-secondary" aria-label="刷新投诉列表" onClick={() => void refresh()} loading={loading}>刷新</Button>
    </div>
    <Table<AdminReportSummary> rowKey="id" loading={loading} dataSource={items} scroll={{ x: 860 }} pagination={{ pageSize: 10, showSizeChanger: false }}
      columns={[
        { title: "工单编号", dataIndex: "public_code" },
        { title: "状态", dataIndex: "status", render: (value: ReportStatus) => <Tag color={statusColor[value]}>{reportStatusNames[value]}</Tag> },
        { title: "对象", dataIndex: "target_label" },
        { title: "提交人", dataIndex: "submitter_user_id", render: (value: string | null) => value || "匿名" },
        { title: "更新时间", dataIndex: "updated_at", render: (value: string) => new Date(value).toLocaleString() },
        { title: "操作", render: (_, row) => <Button type="link" onClick={() => void open(row.id)}>处理</Button> },
      ]}
    />
    <Drawer title={detail ? detail.public_code : "投诉详情"} open={detail !== null} onClose={() => { if (!busy) setDetail(null); }} width="min(100vw, 560px)" destroyOnHidden>
      {detail && target ? <div className="report-detail">
        {error ? <Alert type="error" title={error} showIcon /> : null}
        <p>状态：{reportStatusNames[detail.status]} · 更新于 {new Date(detail.updated_at).toLocaleString()}</p>
        <p>提交人：{detail.submitter_user_id || "匿名"}{detail.contact ? ` · 联系方式 ${detail.contact}` : ""}</p>
        {detail.accepted_at ? <p>受理人：{detail.assignee_user_id} · {new Date(detail.accepted_at).toLocaleString()}</p> : null}
        {detail.resolved_at ? <p>办结时间：{new Date(detail.resolved_at).toLocaleString()}</p> : null}
        <p>原因：{reportReasons[detail.reason]}</p>
        <p>说明：{detail.description}</p>
        <section>
          <h3 className="report-section-title">被举报对象</h3>
          <p>{target.label}</p>
          {target.message_id ? <p>会话 {target.conversation_id} · 消息 {target.message_id} · 序号 {target.message_seq}</p> : null}
          {target.revision_id ? <p>知识库 {target.kb_id} · 文档 {target.document_id} · 第 {target.revision_no} 版 · 修订 {target.revision_id}</p> : null}
          {target.source_hash ? <p>原件摘要：{target.source_hash}</p> : null}
          <p className="report-live">{liveText(detail)}</p>
          {target.excerpt ? <p>留存摘要：{target.excerpt}</p> : null}
          {target.message_id && target.live?.hash_matches && target.live.status === "completed"
            ? <ReportedMessage key={detail.id} id={detail.id} /> : null}
          {target.kb_id ? <p className="report-note">如需暂停共享，请在知识库管理中处理，并将实际措施记录到工单。</p> : null}
        </section>
        <section>
          <h3 className="report-section-title">处理记录</h3>
          <ul>{detail.events.map((event) => <li key={`${event.action}-${event.created_at}`}>
            {reportEventName(event.action)} · {new Date(event.created_at).toLocaleString()}
            {event.actor_user_id ? ` · ${event.actor_user_id}` : ""}
            {event.internal_note ? ` · 内部备注：${event.internal_note}` : ""}
            {event.feedback ? ` · 反馈：${event.feedback}` : ""}
            {event.measure ? ` · 措施：${event.measure}` : ""}
          </li>)}</ul>
        </section>
        {detail.status !== "resolved" ? <Form form={form} layout="vertical" onFinish={(values) => void run(() => resolveReport(detail.id, values.feedback, values.measure))}>
          {detail.status === "pending" ? <Button type="primary" loading={busy} onClick={() => void run(() => acceptReport(detail.id))}>受理</Button> : <>
            <Form.Item name="note" label="内部备注"><Input.TextArea aria-label="内部备注" maxLength={2000} rows={3} /></Form.Item>
            <Button loading={busy} onClick={() => void run(async () => {
              const next = await noteReport(detail.id, String(form.getFieldValue("note") || ""));
              form.setFieldValue("note", "");
              return next;
            })}>保存内部备注</Button>
            <Form.Item name="feedback" label="给用户的反馈" rules={[{ required: true, whitespace: true, message: "办结时需要填写给用户的反馈" }]}>
              <Input.TextArea aria-label="给用户的反馈" maxLength={2000} rows={3} />
            </Form.Item>
            <Button loading={busy} onClick={() => void run(() => replyReport(detail.id, String(form.getFieldValue("feedback") || "")))}>发送反馈</Button>
            <Form.Item name="measure" label="实际采取的措施" extra="办结只记录措施，不会自动删除消息、封禁账号或下架文档。" rules={[{ required: true, whitespace: true, message: "请填写实际采取的措施" }]}>
              <Input.TextArea aria-label="实际采取的措施" maxLength={500} rows={3} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={busy}>办结</Button>
          </>}
        </Form> : <p>办结反馈：{detail.public_feedback}</p>}
        {detail.measure ? <p>记录的措施：{detail.measure}</p> : null}
      </div> : null}
    </Drawer>
  </div>;
}
