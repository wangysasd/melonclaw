import { App, Form, Modal } from "antd";
import { useState } from "react";
import { apiRequest } from "../api/client";
import { notifyAuthChange } from "../api/auth";
import { PasswordFields } from "./PasswordFields";

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [form] = Form.useForm<{ password: string; confirm_password: string }>();
  const [saving, setSaving] = useState(false);
  const { message } = App.useApp();
  return <Modal open title="修改密码" okText="保存" cancelText="取消" confirmLoading={saving} onCancel={() => { if (!saving) onClose(); }} onOk={() => form.submit()}>
    <Form form={form} layout="vertical" requiredMark={false} onFinish={async (values) => {
      setSaving(true);
      try {
        await apiRequest("/api/auth/password", { method: "POST", body: values });
        message.success("密码已修改，请重新登录");
        onClose();
        notifyAuthChange();
      } catch (err) { message.error(err instanceof Error ? err.message : "修改密码失败"); }
      finally { setSaving(false); }
    }}>
      <PasswordFields />
    </Form>
  </Modal>;
}
