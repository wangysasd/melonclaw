import { Alert, Button, Drawer, Input, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import {
  getMyReport,
  listMyReports,
  reportEventName,
  reportReasons,
  reportStatusNames,
  type ReportDetail,
  type ReportSummary,
} from "../api/reports";
import { PRIVACY_REQUEST_PATH } from "../lib/privacyMail";
import { ReportDialog } from "./ReportDialog";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { Icon } from "./Icon";
import "../styles/reports.css";

const statusColor = { pending: "gold", processing: "blue", resolved: "green" } as const;

function Detail({ detail }: { detail: ReportDetail }) {
  return <dl className="report-detail">
    <div><dt>状态</dt><dd>{reportStatusNames[detail.status]}</dd></div>
    <div><dt>更新时间</dt><dd>{new Date(detail.updated_at).toLocaleString()}</dd></div>
    <div><dt>对象</dt><dd>{detail.target.label}{detail.target.revision_no ? ` · 第 ${detail.target.revision_no} 版` : ""}{detail.target.message_seq ? ` · 第 ${detail.target.message_seq} 条` : ""}</dd></div>
    <div><dt>原因</dt><dd>{reportReasons[detail.reason]}</dd></div>
    <div><dt>说明</dt><dd>{detail.description}</dd></div>
    <div><dt>处理反馈</dt><dd>{detail.public_feedback || "暂无面向你的反馈"}</dd></div>
    <div><dt>进度</dt><dd><ul>{detail.events.map((event) => <li key={`${event.action}-${event.created_at}`}>{reportEventName(event.action)} · {new Date(event.created_at).toLocaleString()}{event.feedback ? ` · ${event.feedback}` : ""}</li>)}</ul></dd></div>
  </dl>;
}

export function MyReports({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [creating, setCreating] = useState(false);
  async function refresh() {
    setLoading(true);
    try {
      setItems((await listMyReports()).items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  async function open(publicCode: string) {
    setError("");
    try { setDetail(await getMyReport(publicCode.trim())); }
    catch (err) { setDetail(null); setError(err instanceof Error ? err.message : "未找到该投诉举报。"); }
  }
  const query = code.trim().toUpperCase();
  const visibleItems = items.filter((item) => item.public_code.includes(query));

  return <section className="account-page report-page">
    <WorkspaceHeader title="意见反馈" icon="circle-alert" actions={<Button className="action-secondary" icon={<Icon name="arrow-up" size={16} rotate={-90} />} onClick={onClose}>返回聊天</Button>} />
    <div className="account-page-content">
      {error ? <Alert type="error" title={error} showIcon /> : null}
      <div className="account-toolbar resource-actions skill-resource-actions">
        <Input
          className="skill-search"
          prefix={<Icon name="search" size={14} />}
          allowClear
          aria-label="搜索工单编号"
          placeholder="搜索工单编号，回车查询"
          value={code}
          maxLength={32}
          enterKeyHint="search"
          onChange={(event) => { setCode(event.target.value); setPage(1); }}
          onPressEnter={() => { if (query) void open(query); }}
        />
        <div className="skill-actions-trailing">
          <Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => setCreating(true)}>提交反馈</Button>
          <Button className="action-secondary" aria-label="刷新反馈列表" icon={<Icon name="refresh-cw" size={14} />} onClick={() => void refresh()} loading={loading} />
        </div>
      </div>
      <Table<ReportSummary> rowKey="public_code" loading={loading} dataSource={visibleItems} scroll={{ x: 720 }}
        pagination={{ current: page, pageSize: 10, showSizeChanger: false, onChange: setPage }}
        columns={[
          { title: "工单编号", dataIndex: "public_code" },
          { title: "状态", dataIndex: "status", render: (status: ReportSummary["status"]) => <Tag color={statusColor[status]}>{reportStatusNames[status]}</Tag> },
          { title: "对象", render: (_, row) => row.target.label },
          { title: "更新时间", dataIndex: "updated_at", render: (value: string) => new Date(value).toLocaleString() },
          { title: "操作", render: (_, row) => <Button type="link" onClick={() => void open(row.public_code)}>查看</Button> },
        ]}
      />
      <p className="report-support-link"><a href={PRIVACY_REQUEST_PATH}>个人信息申请</a></p>
    </div>
    <Drawer title={detail ? detail.public_code : "反馈详情"} open={detail !== null} onClose={() => setDetail(null)} width="min(100vw, 520px)">
      {detail ? <Detail detail={detail} /> : null}
    </Drawer>
    {creating ? <ReportDialog
      onClose={() => setCreating(false)}
      onSubmitted={() => { setCode(""); setPage(1); void refresh(); }}
      onViewProgress={(publicCode) => { setCreating(false); void open(publicCode); }}
    /> : null}
  </section>;
}
