import { Form, Input } from "antd";

export function PasswordFields({ label = "新密码" }: { label?: string }) {
  return <>
    <Form.Item name="password" label={label} rules={[{ required: true, min: 5, max: 128, message: "密码需要 5～128 个字符" }]}>
      <Input.Password autoComplete="new-password" maxLength={128} />
    </Form.Item>
    <Form.Item name="confirm_password" label="再次输入密码" dependencies={["password"]} rules={[
      { required: true, message: "请再次输入密码" },
      ({ getFieldValue }) => ({ validator: (_, value) => !value || getFieldValue("password") === value ? Promise.resolve() : Promise.reject(new Error("两次输入的密码不一致")) }),
    ]}>
      <Input.Password autoComplete="new-password" maxLength={128} />
    </Form.Item>
  </>;
}
