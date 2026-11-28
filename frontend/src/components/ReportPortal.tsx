import { Alert, Button, Form, Input } from "antd";
import { useEffect, useRef, useState } from "react";
import {
  lookupReport,
  reportChannels,
  reportEventName,
  reportStatusNames,
  submitAnonymousReport,
  type ReportChannels,
  type ReportDetail,
  type ReportSubmitResult,
} from "../api/reports";
import { copyText } from "../lib/clipboard";
import { FeedbackForm } from "./FeedbackForm";
import { Icon } from "./Icon";
import "../styles/reports.css";

function ReportChannelsNotice({ channels }: { channels: ReportChannels | null }) {
  if (channels === null) return null;
  if (!channels.email) {
    return <p className="report-note">可以通过下方站内表单提交。当前未配置对外联系渠道。</p>;
  }
  return <section className="report-channels" aria-label="运营联系渠道">
    <h2>联系运营方</h2>
    {channels.contact_name ? <p>联系人：{channels.contact_name}</p> : null}
    <p>投诉邮箱：<a href={`mailto:${channels.email}`}>{channels.email}</a></p>
    {channels.response_hours ? <p>承诺在 {channels.response_hours} 小时内响应。</p> : null}
  </section>;
}

function SavedTicket({ result }: { result: ReportSubmitResult }) {
  const [copied, setCopied] = useState("复制编号和查询码");
  const receipt = useRef<HTMLElement>(null);
  useEffect(() => { receipt.current?.focus(); }, []);
  const text = `工单编号：${result.public_code}\n查询码：${result.lookup_code}`;
  return <section className="report-secret" aria-label="提交凭据" tabIndex={-1} ref={receipt}>
    <p>{result.notice}</p>
    <p>工单编号：{result.public_code}</p>
    <p>查询码：{result.lookup_code}</p>
    <Button className="action-secondary" onClick={() => void copyText(text).then(() => setCopied("已复制"), () => setCopied("复制失败"))}>{copied}</Button>
  </section>;
}

function LookupResult({ detail }: { detail: ReportDetail }) {
  const result = useRef<HTMLElement>(null);
  useEffect(() => { result.current?.focus(); }, [detail]);
  return <section className="report-detail" aria-label="投诉进度" tabIndex={-1} ref={result}>
    <p>工单编号：{detail.public_code}</p>
    <p>状态：{reportStatusNames[detail.status]}</p>
    <p>更新时间：{new Date(detail.updated_at).toLocaleString()}</p>
    <p>说明：{detail.description}</p>
    <p>处理反馈：{detail.public_feedback || "暂无面向你的反馈"}</p>
    <ul>
      {detail.events.map((event) => <li key={`${event.action}-${event.created_at}`}>
        {reportEventName(event.action)} · {new Date(event.created_at).toLocaleString()}
        {event.feedback ? ` · ${event.feedback}` : ""}
      </li>)}
    </ul>
  </section>;
}

export function ReportPortal({ onBack }: { onBack: () => void }) {
  const [channels, setChannels] = useState<ReportChannels | null>(null);
  const [saved, setSaved] = useState<ReportSubmitResult | null>(null);
  const [found, setFound] = useState<ReportDetail | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [lookupError, setLookupError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [lookingUp, setLookingUp] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void reportChannels(controller.signal).then(setChannels).catch(() => {
      if (!controller.signal.aborted) setChannels({});
    });
    return () => controller.abort();
  }, []);
  return <div className="report-portal">
    <div className="report-heading">
      <h2>意见反馈</h2>
      <Button className="action-secondary report-back" icon={<Icon name="arrow-up" size={16} rotate={-90} />} disabled={submitting} onClick={onBack}>返回登录</Button>
    </div>
    <p>可提交问题、建议、投诉或举报。提交后请保存工单编号和查询码，管理员核查后会在工单中反馈结果。</p>
    <ReportChannelsNotice channels={channels} />
    {saved ? <SavedTicket result={saved} /> : <FeedbackForm block submitting={submitting} error={submitError} onSubmit={(values) => {
      if (submitting) return;
      setSubmitting(true);
      setSubmitError("");
      void submitAnonymousReport(values).then((result) => {
        setSaved(result);
        setFound(null);
      }).catch((err: unknown) => {
        setSubmitError(err instanceof Error ? err.message : "提交失败，请稍后重试。");
      }).finally(() => setSubmitting(false));
    }} />}
    <h2 className="report-section-title" style={{ marginTop: 24 }}>查询进度</h2>
    <Form layout="vertical" requiredMark={false} onFinish={(values) => {
      if (lookingUp) return;
      setLookingUp(true); setLookupError(""); setFound(null);
      void lookupReport({ public_code: values.public_code, lookup_code: values.lookup_code }).then((detail) => {
        setFound(detail);
      }).catch((err: unknown) => {
        setLookupError(err instanceof Error ? err.message : "查询失败，请稍后重试。");
      }).finally(() => setLookingUp(false));
    }}>
      <Form.Item name="public_code" label="工单编号" rules={[{ required: true, whitespace: true, message: "请输入工单编号" }]}>
        <Input aria-label="工单编号" maxLength={32} autoComplete="off" />
      </Form.Item>
      <Form.Item name="lookup_code" label="查询码" rules={[{ required: true, whitespace: true, message: "请输入查询码" }]}>
        <Input.Password aria-label="查询码" maxLength={80} autoComplete="off" />
      </Form.Item>
      {lookupError ? <Alert type="error" title={lookupError} showIcon /> : null}
      <Button className="action-secondary" htmlType="submit" loading={lookingUp} block>查询</Button>
    </Form>
    {found ? <LookupResult detail={found} /> : null}
  </div>;
}
