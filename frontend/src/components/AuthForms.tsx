import { Alert, Button, Form, Input, Popover } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import {
  getAuthConfig, login, loginWithSms, passwordlessLogin, registerAccount, resetPasswordWithSms, sendSmsChallenge,
} from "../api/auth";
import {
  clearLoginDraft, readLoginDraft, remainingSeconds, writeLoginDraft, type LoginDraft, type LoginMode,
} from "../lib/loginDraft";
import { SmsPurposeNote } from "./SmsPurposeNote";

export function AuthPanel({
  onRegisterDraftSaver,
}: {
  onRegisterDraftSaver?: (saver: (() => void) | null) => void;
}) {
  const [form] = Form.useForm();
  const [mode, setMode] = useState<LoginMode>("password");
  const [passwordless, setPasswordless] = useState(false);
  const [registration, setRegistration] = useState(false);
  const [smsLogin, setSmsLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [wait, setWait] = useState(0);
  const pending = useRef<LoginDraft | null>(null);
  const prefillUser = useRef("");
  const restored = useRef(false);

  useEffect(() => {
    let active = true;
    void getAuthConfig().then((config) => {
      if (!active) return;
      setPasswordless(config.passwordless);
      setRegistration(Boolean(config.registration));
      setSmsLogin(Boolean(config.sms_login));
      if (restored.current) return;
      restored.current = true;
      const draft = readLoginDraft();
      const allowed = draft && (
        draft.mode === "password"
        || ((draft.mode === "sms" || draft.mode === "reset") && config.sms_login)
        || (draft.mode === "register" && config.registration)
      );
      if (!draft || !allowed) return;
      setChallengeId(draft.challengeId);
      setWait(remainingSeconds(draft.waitUntil));
      if (draft.mode === "password") {
        form.setFieldsValue({
          login_identifier: draft.login_identifier,
          login_secret: draft.login_secret,
          confirm_secret: draft.confirm_secret,
          phone_number: draft.phone_number,
          verify_code: draft.verify_code,
        });
      } else {
        pending.current = draft;
        setMode(draft.mode);
      }
    }).catch(() => { if (active) setError("无法连接服务，请稍后刷新重试。"); });
    return () => { active = false; };
  }, [form]);

  useEffect(() => {
    if (mode === "password" && prefillUser.current) {
      form.setFieldValue("login_identifier", prefillUser.current);
      prefillUser.current = "";
    }
    const draft = pending.current;
    if (!draft || draft.mode !== mode) return;
    form.setFieldsValue({
      login_identifier: draft.login_identifier,
      login_secret: draft.login_secret,
      confirm_secret: draft.confirm_secret,
      phone_number: draft.phone_number,
      verify_code: draft.verify_code,
    });
    pending.current = null;
  }, [form, mode]);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setTimeout(() => setWait((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [wait]);

  function clearChallenge() {
    setChallengeId("");
    setWait(0);
    form.setFieldValue("verify_code", "");
  }

  function switchMode(next: LoginMode) {
    setMode(next);
    setError("");
    setNotice("");
    setChallengeId("");
    setWait(0);
    form.resetFields();
  }

  function rememberDraft() {
    const values = form.getFieldsValue();
    writeLoginDraft({
      mode,
      login_identifier: String(values.login_identifier ?? ""),
      login_secret: String(values.login_secret ?? ""),
      confirm_secret: String(values.confirm_secret ?? ""),
      phone_number: String(values.phone_number ?? ""),
      verify_code: String(values.verify_code ?? ""),
      challengeId,
      waitUntil: wait > 0 ? Date.now() + wait * 1000 : 0,
    });
  }

  const rememberDraftRef = useRef(rememberDraft);
  rememberDraftRef.current = rememberDraft;
  useEffect(() => {
    if (!onRegisterDraftSaver) return;
    onRegisterDraftSaver(() => rememberDraftRef.current());
    return () => onRegisterDraftSaver(null);
  }, [onRegisterDraftSaver]);

  async function sendCode() {
    const raw = String(form.getFieldValue("phone_number") || "").trim();
    if (!raw) { setError("请输入手机号"); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const purpose = mode === "register" ? "register" : mode === "reset" ? "reset" : "login";
      const result = await sendSmsChallenge(raw, purpose);
      setChallengeId(result.challenge_id);
      setWait(result.resend_after);
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败，请稍后重试。");
    } finally { setBusy(false); }
  }

  async function signInWithoutPassword() {
    setBusy(true); setError(""); setNotice("");
    try {
      await passwordlessLogin();
      clearLoginDraft();
    } catch (err) { setError(err instanceof Error ? err.message : "登录失败，请重试。"); }
    finally { setBusy(false); }
  }

  async function submit(values: Record<string, string>) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (mode === "password") {
        await login(values.login_identifier.trim(), values.login_secret);
        clearLoginDraft();
      } else if (!challengeId) {
        setError("请先获取验证码");
      } else if (mode === "sms") {
        await loginWithSms(String(values.phone_number || "").trim(), challengeId, values.verify_code);
        clearLoginDraft();
      } else if (mode === "reset") {
        const result = await resetPasswordWithSms({
          phone: String(values.phone_number || "").trim(),
          challenge_id: challengeId,
          code: values.verify_code,
          password: values.login_secret,
          confirm_password: values.confirm_secret,
        });
        clearLoginDraft();
        prefillUser.current = result.user_id || "";
        setMode("password");
        setNotice("密码已重置，请使用新密码登录。");
        setChallengeId("");
        setWait(0);
        form.resetFields();
      } else {
        await registerAccount({
          user_id: values.login_identifier.trim(),
          password: values.login_secret,
          confirm_password: values.confirm_secret,
          phone: String(values.phone_number || "").trim(),
          challenge_id: challengeId,
          code: values.verify_code,
        });
        clearLoginDraft();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试。");
    } finally { setBusy(false); }
  }

  const smsNoteText = mode === "reset"
    ? "手机号用于找回密码验证，阿里云发码并核验。"
    : "手机号用于注册和登录，阿里云发码并核验。";

  const phoneFields = (
    <>
      <Form.Item name="phone_number" label={<span>手机号 <Popover classNames={{ root: "auth-info-popover" }} trigger="click" content={<SmsPurposeNote onOpen={rememberDraft}>{smsNoteText}</SmsPurposeNote>}><button type="button" className="auth-info-button" aria-label="手机号用途说明" onClick={(event) => event.preventDefault()}><InfoCircleOutlined /></button></Popover></span>} rules={[{ required: true, whitespace: true, message: "请输入手机号" }]}>
        <Input aria-label="手机号" inputMode="tel" autoComplete="tel" maxLength={20} placeholder="请输入中国大陆手机号" onChange={clearChallenge} />
      </Form.Item>
      <Form.Item label="验证码" required>
        <div className="auth-code-row">
          <Form.Item name="verify_code" noStyle rules={[{ required: true, message: "请输入验证码" }]}>
            <Input aria-label="验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6 位验证码" />
          </Form.Item>
          <Button className="action-secondary auth-code-button" htmlType="button" disabled={busy || wait > 0} onClick={() => void sendCode()}>
            {wait > 0 ? `${wait} 秒后重发` : challengeId ? "重新获取" : "获取验证码"}
          </Button>
        </div>
      </Form.Item>
    </>
  );

  const isLoginView = mode === "password" || mode === "sms";
  const showForgot = isLoginView && smsLogin;
  const showRegister = isLoginView && registration;

  return (
    <div className={`auth-panel is-${mode}`}>
      {smsLogin && isLoginView ? (
        <div className="auth-mode-tabs" role="group" aria-label="登录方式">
          <button
            type="button"
            className={`auth-mode-tab${mode === "password" ? " is-active" : ""}`}
            aria-pressed={mode === "password"}
            disabled={busy}
            onClick={() => { if (mode !== "password") switchMode("password"); }}
          >
            账号密码登录
          </button>
          <button
            type="button"
            className={`auth-mode-tab${mode === "sms" ? " is-active" : ""}`}
            aria-pressed={mode === "sms"}
            disabled={busy}
            onClick={() => { if (mode !== "sms") switchMode("sms"); }}
          >
            短信验证码登录
          </button>
        </div>
      ) : null}

      {mode === "register" || mode === "reset" ? (
        <div className="auth-register-bar">
          <span className="auth-register-title">{mode === "reset" ? "找回密码" : "创建新账号"}</span>
        </div>
      ) : null}

      {notice ? <Alert type="success" title={notice} showIcon /> : null}
      {error ? <Alert type="error" title={error} showIcon /> : null}

      <Form form={form} layout="vertical" autoComplete="off" requiredMark={false} onFinish={(values) => void submit(values)}>
        {mode === "reset" ? phoneFields : null}
        {mode === "password" || mode === "register" ? (
          <Form.Item name="login_identifier" label="用户 ID" rules={[{ required: true, whitespace: true, message: "请输入用户 ID" }]}>
            <Input autoComplete="off" maxLength={64} autoFocus placeholder="请输入用户 ID" />
          </Form.Item>
        ) : null}
        {mode !== "sms" ? (
          <Form.Item
            name="login_secret"
            label={mode === "reset" ? "新密码" : "密码"}
            rules={mode === "reset"
              ? [{ required: true, min: 5, max: 128, message: "密码需要 5～128 个字符" }]
              : [{ required: true, message: "请输入密码" }]}
          >
            <Input.Password autoComplete="new-password" maxLength={128} placeholder={mode === "reset" ? "请输入 5～128 位新密码" : "请输入密码"} />
          </Form.Item>
        ) : null}
        {mode === "register" || mode === "reset" ? (
          <Form.Item
            name="confirm_secret"
            label={mode === "reset" ? "确认新密码" : "再次输入密码"}
            dependencies={["login_secret"]}
            rules={[
              { required: true, message: mode === "reset" ? "请再次输入新密码" : "请再次输入密码" },
              ({ getFieldValue }) => ({
                validator: (_, value) => (!value || getFieldValue("login_secret") === value
                  ? Promise.resolve()
                  : Promise.reject(new Error("两次输入的密码不一致"))),
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" maxLength={128} placeholder={mode === "reset" ? "请再次输入新密码" : "请再次输入密码"} />
          </Form.Item>
        ) : null}
        {mode === "sms" || mode === "register" ? phoneFields : null}

        {mode !== "reset" ? (
          <p className="auth-agreement">注册登录即代表已阅读并同意 <a href="/terms" onClick={rememberDraft}>用户协议</a> 与 <a href="/privacy" onClick={rememberDraft}>隐私政策</a></p>
        ) : null}
        <div className="auth-actions">
          <Button type="primary" className="login-submit" htmlType="submit" loading={busy}>
            {mode === "register" ? "注册并登录" : mode === "reset" ? "重置密码" : "登录"}
          </Button>
          {mode === "password" && passwordless ? (
            <button type="button" className="auth-inline-link passwordless-link" disabled={busy} onClick={() => void signInWithoutPassword()}>
              免密登录
            </button>
          ) : null}
        </div>
      </Form>

      {mode === "register" || mode === "reset" ? (
        <div className="auth-switch-row">
          <span className="auth-switch-hint">{mode === "reset" ? "记起密码了？" : "已有账号？"}</span>
          <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("password")}>
            账号密码登录
          </button>
          {smsLogin ? (
            <>
              <span className="auth-switch-sep" aria-hidden="true">·</span>
              <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("sms")}>
                短信验证码登录
              </button>
            </>
          ) : null}
        </div>
      ) : showForgot && showRegister ? (
        <div className="auth-switch-row is-split">
          <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("reset")}>
            忘记密码
          </button>
          <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("register")}>
            注册账号
          </button>
        </div>
      ) : showForgot ? (
        <div className="auth-switch-row">
          <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("reset")}>
            忘记密码
          </button>
        </div>
      ) : showRegister ? (
        <div className="auth-switch-row">
          <span className="auth-switch-hint">还没有账号？</span>
          <button type="button" className="auth-inline-link" disabled={busy} onClick={() => switchMode("register")}>
            注册账号
          </button>
        </div>
      ) : null}
    </div>
  );
}
