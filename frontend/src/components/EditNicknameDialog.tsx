import { App, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { updateProfile } from "../api/auth";

export function EditNicknameDialog({
  open,
  currentName,
  onClose,
  onSaved,
}: {
  open: boolean;
  currentName: string;
  onClose: () => void;
  onSaved: (name: string) => Promise<void> | void;
}) {
  const [form] = Form.useForm<{ user_name_zh: string }>();
  const [saving, setSaving] = useState(false);
  const { message } = App.useApp();

  useEffect(() => {
    if (open) form.setFieldsValue({ user_name_zh: currentName });
  }, [open, currentName, form]);

  return (
    <Modal
      open={open}
      destroyOnHidden
      title="修改昵称"
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={() => { if (!saving) onClose(); }}
      onOk={() => form.submit()}
    >
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ user_name_zh: currentName }}
        onFinish={async (values) => {
          setSaving(true);
          try {
            const result = await updateProfile({ user_name_zh: values.user_name_zh.trim() });
            await Promise.resolve(onSaved(result.user_name_zh)).catch(() => undefined);
            message.success("昵称已修改");
            onClose();
          } catch (err) {
            message.error(err instanceof Error ? err.message : "修改昵称失败");
          } finally {
            setSaving(false);
          }
        }}
      >
        <Form.Item
          name="user_name_zh"
          label="用户昵称"
          rules={[
            { required: true, whitespace: true, message: "请输入用户昵称" },
            { max: 64, message: "昵称最多 64 个字符" },
          ]}
        >
          <Input
            autoFocus
            maxLength={64}
            placeholder="请输入用户昵称"
            onPressEnter={() => form.submit()}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
