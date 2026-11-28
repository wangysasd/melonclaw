import { App, Button, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { bindPhone, sendBindChallenge } from "../api/auth";
import { clearBindDraft, readBindDraft, remainingSeconds, writeBindDraft } from "../lib/loginDraft";
import { SmsPurposeNote } from "./SmsPurposeNote";

export function BindPhoneDialog({ onClose, onBound }: { onClose: () => void; onBound: (tail: string) => void }) {
  const [form] = Form.useForm<{ password: string; phone_number: string; verify_code: string }>();
  const [draft] = useState(readBindDraft);
  const [saving, setSaving] = useState(false);
  const [challengeId, setChallengeId] = useState(draft?.challengeId ?? "");
  const [wait, setWait] = useState(remainingSeconds(draft?.waitUntil ?? 0));
  const { message } = App.useApp();
  useEffect(() => {
    if (!draft) return;
    form.setFieldsValue({
      password: draft.password,
      phone_number: draft.phone_number,
      verify_code: draft.verify_code,
    });
  }, [draft, form]);
  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setTimeout(() => setWait((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [wait]);
  async function sendCode() {
    const phone = String(form.getFieldValue("phone_number") || "").trim();
    if (!phone) { message.error("请输入手机号"); return; }
    setSaving(true);
    try {
      const result = await sendBindChallenge(phone);
      setChallengeId(result.challenge_id);
      setWait(result.resend_after);
    } catch (err) { message.error(err instanceof Error ? err.message : "验证码发送失败"); }
    finally { setSaving(false); }
  }
  function rememberDraft() {
    const values = form.getFieldsValue();
    writeBindDraft({
      password: String(values.password ?? ""),
      phone_number: String(values.phone_number ?? ""),
      verify_code: String(values.verify_code ?? ""),
      challengeId,
      waitUntil: wait > 0 ? Date.now() + wait * 1000 : 0,
    });
  }
  function close() {
    clearBindDraft();
    onClose();
  }
  return <Modal open title="绑定手机号" okText="绑定" cancelText="取消" confirmLoading={saving} onCancel={() => { if (!saving) close(); }} onOk={() => form.submit()}>
    <Form form={form} layout="vertical" autoComplete="off" requiredMark={false} onFinish={async (values) => {
      if (!challengeId) { message.error("请先获取验证码"); return; }
      setSaving(true);
      try {
        const result = await bindPhone({
          password: values.password, phone: values.phone_number.trim(), challenge_id: challengeId, code: values.verify_code,
        });
        message.success(`已绑定尾号 ${result.phone_tail}`);
        onBound(result.phone_tail);
        clearBindDraft();
        onClose();
      } catch (err) { message.error(err instanceof Error ? err.message : "绑定失败"); }
      finally { setSaving(false); }
    }}>
      <Form.Item name="password" label="当前密码" rules={[{ required: true, message: "请输入当前密码" }]}>
        <Input.Password autoComplete="new-password" maxLength={128} />
      </Form.Item>
      <Form.Item name="phone_number" label="手机号" rules={[{ required: true, whitespace: true, message: "请输入手机号" }]}>
        <Input inputMode="tel" autoComplete="tel" maxLength={20} onChange={() => { setChallengeId(""); setWait(0); form.setFieldValue("verify_code", ""); }} />
      </Form.Item>
      <SmsPurposeNote onOpen={rememberDraft}>手机号用于绑定当前账号，并可用于之后的短信登录。验证码由阿里云发送并核验。</SmsPurposeNote>
      <Form.Item label="验证码" required>
        <div className="auth-code-row">
          <Form.Item name="verify_code" noStyle rules={[{ required: true, message: "请输入验证码" }]}>
            <Input aria-label="验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} />
          </Form.Item>
          <Button className="auth-code-button" htmlType="button" disabled={saving || wait > 0} onClick={() => void sendCode()}>
            {wait > 0 ? `${wait} 秒后重发` : challengeId ? "重新获取" : "获取验证码"}
          </Button>
        </div>
      </Form.Item>
    </Form>
  </Modal>;
}
