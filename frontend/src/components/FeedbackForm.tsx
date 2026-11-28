import { Alert, Button, Form, Input, Select } from "antd";
import { generalReasons, reportReasons, type ReportReason } from "../api/reports";

export interface FeedbackValues {
  reason: ReportReason;
  description: string;
  contact?: string;
}

const reasonOptions = generalReasons.map((value) => ({ value, label: reportReasons[value] }));

export function FeedbackForm({ onSubmit, submitting, error, block }: {
  onSubmit: (values: FeedbackValues) => void;
  submitting: boolean;
  error: string;
  block?: boolean;
}) {
  return <Form<FeedbackValues> layout="vertical" requiredMark={false} onFinish={onSubmit}>
    <Form.Item name="reason" label="原因" rules={[{ required: true, message: "请选择原因" }]}>
      <Select aria-label="反馈原因" virtual={false} options={reasonOptions} placeholder="请选择原因" />
    </Form.Item>
    <Form.Item name="description" label="说明"
      extra="投诉举报时，可提供文档名、账号名称、发生时间或必要的内容片段。请勿填写密码或验证码。"
      rules={[{ required: true, whitespace: true, message: "请填写说明" }]}
    >
      <Input.TextArea aria-label="反馈说明" maxLength={2000} showCount rows={4} placeholder="请描述遇到的问题" />
    </Form.Item>
    <Form.Item name="contact" label="联系方式（可选）">
      <Input aria-label="联系方式" maxLength={80} autoComplete="off" placeholder="邮箱或电话，可不填" />
    </Form.Item>
    {error ? <Alert type="error" title={error} showIcon /> : null}
    <Button type="primary" htmlType="submit" loading={submitting} block={block}>提交反馈</Button>
  </Form>;
}
