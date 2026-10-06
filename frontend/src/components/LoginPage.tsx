import { Alert, Button, ConfigProvider, Form, Input } from "antd";
import { useEffect, useState } from "react";
import { antdTheme } from "../theme/antd";
import { getAuthConfig, login, passwordlessLogin } from "../api/auth";

export function LoginPage() {
  const [passwordless, setPasswordless] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void getAuthConfig().then((config) => { if (active) setPasswordless(config.passwordless); })
      .catch(() => { if (active) setError("无法连接服务，请稍后刷新重试。"); });
    return () => { active = false; };
  }, []);
  async function submit(values?: { user_id: string; password: string }) {
    setBusy(true); setError("");
    try {
      if (values) await login(values.user_id.trim(), values.password);
      else await passwordlessLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试。");
    } finally { setBusy(false); }
  }
  const isRmsBrand = __MELONCLAW_IS_RMS_BRAND__;
  const page = <main className={isRmsBrand ? "login-page rms-login-page" : "login-page melon-login-page"}>
    {isRmsBrand ? <section className="rms-login-visual" aria-label="RMS">
      <img className="rms-login-background" src="/assets/brand/rms-login-glass.webp" alt="" />
      <h1 className="rms-login-headline">让研究更进一步</h1>
    </section> : null}
    <div className={isRmsBrand ? "rms-login-content" : "melon-login-content"}>
    <section className={isRmsBrand ? "rms-login-form" : "melon-login-form"} aria-label={isRmsBrand ? "登录 RMS" : "登录 MelonClaw"}>
      {isRmsBrand ? <span className="rms-login-logo rms-login-form-logo">
        <img src="/assets/brand/melonclaw-word-rms.png" alt="RMS" />
      </span> : <>
        <span className="melon-login-logo"><img src="/assets/brand/melonclaw-word.png" alt="MelonClaw" /></span>
        <header className="melon-login-heading"><h1>开启智能协作</h1></header>
      </>}
      {error ? <Alert type="error" title={error} showIcon /> : null}
      <Form layout="vertical" autoComplete="off" onFinish={(values) => void submit({ user_id: values.login_identifier, password: values.login_secret })} requiredMark={false}>
        <Form.Item name="login_identifier" label="用户 ID" rules={[{ required: true, whitespace: true, message: "请输入用户 ID" }]}>
          <Input autoComplete="off" maxLength={64} autoFocus placeholder="请输入用户 ID" />
        </Form.Item>
        <Form.Item name="login_secret" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password autoComplete="new-password" maxLength={128} placeholder="请输入密码" />
        </Form.Item>
        <Button type="primary" className="login-submit" htmlType="submit" loading={busy}>登录</Button>
        {passwordless ? <Button block type="link" className="passwordless-button" disabled={busy} onClick={() => void submit()}>免密登录</Button> : null}
      </Form>
    </section>
    </div>
    {!isRmsBrand ? <div className="melon-login-visual" aria-hidden="true"><img src="/assets/brand/melonclaw-login-glass.webp" alt="" /></div> : null}
  </main>;
  return <ConfigProvider theme={antdTheme}>{page}</ConfigProvider>;
}
