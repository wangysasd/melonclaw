import { Fragment, useEffect, useRef, useState } from "react";
import { Alert, App, Button, Empty, Popover, Space, Spin, Table, Tag, Typography } from "antd";
import { knowledgeApi, type KnowledgeBase, type KnowledgeDocument, type KnowledgeDocumentJob } from "../api/knowledge";
import { deleteConfirmation } from "./deleteConfirmation";
import { Icon } from "./Icon";

const stages: Record<string, string> = { parsing: "解析", chunking: "分块", embedding: "向量化", publishing: "发布" };
const sourceNames: Record<string, string> = { queued: "排队", running: "处理中", succeeded: "可检索", failed: "失败", cancelled: "已取消" };
const statusColors: Record<string, string> = { queued: "gold", running: "blue", succeeded: "green", failed: "red", cancelled: "orange" };
const stageColors: Record<string, string> = { parsing: "cyan", chunking: "geekblue", embedding: "purple", publishing: "magenta" };
const statusWidth = 176;

const documentFormats = [
  {
    type: "纯文本",
    extensions: [".txt", ".md"],
  },
  {
    type: "PDF、Word、PowerPoint",
    extensions: [".pdf", ".doc", ".docx", ".ppt", ".pptx"],
  },
  {
    type: "图片",
    extensions: [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"],
  },
];
const documentAccept = documentFormats.flatMap(({ extensions }) => extensions).join(",");
const formatHelp = (
  <table className="knowledge-format-help" aria-label="支持的文档格式">
    <thead>
      <tr>
        <th scope="col">类型</th>
        <th scope="col">扩展名</th>
      </tr>
    </thead>
    <tbody>
      {documentFormats.map(({ type, extensions }) => (
        <tr key={type}>
          <th scope="row">{type}</th>
          <td>
            {extensions.map((extension, index) => (
              <Fragment key={extension}>
                {index > 0 ? <>、<wbr /></> : null}
                {extension}
              </Fragment>
            ))}
          </td>
        </tr>
      ))}
    </tbody>
  </table>
);

function jobColor(job: KnowledgeDocumentJob) {
  return (job.status === "running" && job.stage ? stageColors[job.stage] : undefined)
    ?? statusColors[job.status];
}

function hasProcessingAction(job?: KnowledgeDocumentJob | null) {
  return job != null && ["queued", "running", "failed", "cancelled"].includes(job.status);
}

function stageText(job: KnowledgeDocumentJob) {
  return job.status === "running" && job.stage ? ` · ${stages[job.stage] ?? job.stage}` : "";
}

export function KnowledgeDocuments({ base }: { base: KnowledgeBase }) {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [pending, setPending] = useState<{ file: File; requestId: string; documentId?: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const replacing = useRef<string>();
  const [activePipeline, setActivePipeline] = useState<string | null>(base.active_pipeline_job_id);
  const editable = base.role !== "viewer";
  const blocked = Boolean(activePipeline);
  const hasProcessingActions = editable && rows.some((doc) =>
    doc.active_revision_id && hasProcessingAction(doc.source_job));
  const actionsWidth = editable ? (hasProcessingActions ? 350 : 254) : 116;
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await knowledgeApi.documents(base.id, page, controller.signal);
        if (stopped) return;
        setRows(result.items); setTotal(result.total); setActivePipeline(result.active_pipeline_job_id); setError("");
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : "文档读取失败"); }
      finally {
        inFlight = false;
        if (!stopped) setLoading(false);
      }
    };
    setLoading(true);
    void load();
    const interval = window.setInterval(() => void load(), 5000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      controller.abort();
    };
  }, [base.id, page, version]);
  const refresh = () => setVersion((v) => v + 1);
  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); refresh(); } catch (e) { message.error(e instanceof Error ? e.message : "操作失败"); }
  };
  const upload = async (request: NonNullable<typeof pending>) => {
    setUploading(true); setPending(request);
    try {
      await knowledgeApi.upload(base.id, request.file, request.requestId, request.documentId);
      setPending(null); refresh(); message.success("已进入处理队列");
    } catch (e) { message.error(e instanceof Error ? e.message : "上传失败，可重试同一请求"); }
    finally { setUploading(false); }
  };
  const choose = (documentId?: string) => { replacing.current = documentId; input.current?.click(); };
  return <section aria-label="文档管理">
    <div className="knowledge-doc-toolbar">
      <h3>文档 <span>{total} 份</span></h3>
      <Space wrap>
        {editable ? <>
          <Popover trigger="click" title="支持的格式" content={formatHelp}>
            <Button type="link">支持的格式</Button>
          </Popover>
          <Button type="primary" disabled={uploading || blocked} onClick={() => choose()}>上传文档</Button>
        </> : null}
        {pending ? <Button loading={uploading} onClick={() => void upload(pending)}>重试上传</Button> : null}
        <Button aria-label="刷新文档" icon={<Icon name="refresh-cw" size={16} />} onClick={refresh} />
      </Space>
    </div>
    <input ref={input} type="file" hidden accept={documentAccept} aria-label="选择文档文件" onChange={(e) => {
      const file = e.target.files?.[0]; e.target.value = "";
      if (file) void upload({ file, requestId: crypto.randomUUID(), documentId: replacing.current });
    }} />
    {blocked ? <Alert type="info" showIcon title="本库正在处理资料。已发布原文仍可检索和下载，请等待实际结束后再上传。" /> : null}
    {error ? <Alert type="error" title={error} /> : null}
    {total === 0 ? (
      <div className="knowledge-doc-empty">
        {loading ? <Spin aria-label="正在读取文档" /> : error ? null : <Empty description="暂无文档" />}
      </div>
    ) : <Table rowKey="id" loading={loading} dataSource={rows} tableLayout="fixed" scroll={{ x: 140 + statusWidth + actionsWidth }} pagination={{ current: page, total, pageSize: 20, showSizeChanger: false, onChange: setPage }} columns={[
      { title: "文档", dataIndex: "display_name", ellipsis: { showTitle: false }, render: (name: string) => <Typography.Text className="knowledge-doc-name" ellipsis={{ tooltip: { title: name, placement: "topLeft" } }}>{name}</Typography.Text> },
      { title: "状态", width: statusWidth, className: "knowledge-doc-status-cell", render: (_, doc) => {
        const source = doc.source_job;
        const published = Boolean(doc.active_revision_id);
        const jobLabel = source ? `${sourceNames[source.status] ?? source.status}${stageText(source)}` : "未处理";
        return <div className="knowledge-doc-status" aria-live="polite">
          {published && <Tag color={statusColors.succeeded}>可检索</Tag>}
          {(!published || hasProcessingAction(source)) && <Tag color={source ? jobColor(source) : undefined}>
            {published ? `新版本 · ${jobLabel}` : jobLabel}
          </Tag>}
          {source?.status === "failed" && source.error_summary && <p className="knowledge-doc-error">{source.error_summary}</p>}
        </div>;
      } },
      { title: "操作", width: actionsWidth, className: "knowledge-doc-actions-cell", render: (_, doc) => {
        const sourceLive = ["queued", "running"].includes(doc.source_job?.status ?? "");
        const retry = doc.source_job && ["failed", "cancelled"].includes(doc.source_job.status);
        return <Space className="knowledge-doc-actions">
        {doc.active_revision_id ? <Button onClick={() => void run(() => knowledgeApi.download(base.id, doc))}>下载原件</Button> : null}
        {editable ? <>
          <Button disabled={uploading || blocked || sourceLive} onClick={() => choose(doc.id)}>替换</Button>
          {retry ? <Button disabled={blocked} onClick={() => void run(() => knowledgeApi.documentAction(base.id, doc.id, "retry", crypto.randomUUID()))}>重试原文</Button> : null}
          {sourceLive ? <Button onClick={() => void run(() => knowledgeApi.documentAction(base.id, doc.id, "cancel"))}>取消处理</Button> : null}
          <Button danger disabled={blocked} onClick={() => modal.confirm({ ...deleteConfirmation({ title: "删除文档？", name: doc.display_name, metadata: "知识库文档", icon: <Icon name="book-open" size={20} />, description: "删除后立即停止访问并取消处理，历史聊天中的内容无法追回。" }), onOk: () => run(() => knowledgeApi.documentAction(base.id, doc.id, "delete")) })}>删除</Button>
        </> : null}
      </Space>;
      } },
    ]} />}
  </section>;
}
