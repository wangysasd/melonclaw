import { ConfigProvider } from "antd";
import { useCallback, useRef, useState } from "react";
import { PRIVACY_PATH } from "../lib/privacyMail";
import { antdTheme } from "../theme/antd";
import { AuthPanel } from "./AuthForms";
import { FilingFooter } from "./ComplianceNotice";
import { ReportPortal } from "./ReportPortal";
import "../styles/reports.css";

export function LoginPage({ rms = __MELONCLAW_IS_RMS_BRAND__ }: { rms?: boolean }) {
  const [portal, setPortal] = useState(false);
  const saveDraftRef = useRef<(() => void) | null>(null);
  const registerDraftSaver = useCallback((saver: (() => void) | null) => {
    saveDraftRef.current = saver;
  }, []);

  const page = (
    <main className={rms ? "login-page rms-login-page" : "login-page melon-login-page"}>
      <div className="login-stage">
        <section
          className={rms ? "login-visual rms-login-visual" : "login-visual melon-login-visual"}
          aria-label={rms ? "RMS" : undefined}
          aria-hidden={rms ? undefined : true}
        >
          <img
            className={rms ? "login-visual-img rms-login-background" : "login-visual-img"}
            src={rms ? "/assets/brand/rms-login-glass.webp" : "/assets/brand/melonclaw-login-glass.webp"}
            alt=""
          />
          {rms ? <h1 className="rms-login-headline">让研究更进一步</h1> : null}
        </section>

        <div className={`login-form-pane${portal ? " login-content-scroll" : ""}`}>
          <div className="login-form-body">
            <section
              className={`login-form-card ${rms ? "rms-login-form" : "melon-login-form"}${portal ? " is-portal" : ""}`}
              aria-label={portal ? "意见反馈" : rms ? "登录 RMS" : "登录 MelonClaw"}
            >
              {portal ? (
                <ReportPortal onBack={() => setPortal(false)} />
              ) : (
                <>
                  <header className={rms ? "login-brand-header is-rms" : "login-brand-header"}>
                    {rms ? (
                      <span className="rms-login-logo rms-login-form-logo">
                        <img src="/assets/brand/melonclaw-word-rms.png" alt="RMS" />
                      </span>
                    ) : (
                      <>
                        <span className="melon-login-logo">
                          <img src="/assets/brand/melonclaw-word-transparent.webp" alt="MelonClaw" />
                        </span>
                        <h1 className="melon-login-heading">开启智能协作</h1>
                      </>
                    )}
                  </header>
                  <AuthPanel onRegisterDraftSaver={registerDraftSaver} />
                </>
              )}
            </section>
          </div>

          <div className="login-footer">
            <div className="login-footer-links">
              <a
                className="login-footer-link"
                href={PRIVACY_PATH}
                onClick={() => saveDraftRef.current?.()}
              >
                隐私说明
              </a>
              {!portal ? (
                <>
                  <span className="login-footer-sep" aria-hidden="true">·</span>
                  <button
                    type="button"
                    className="login-footer-link login-report-entry"
                    onClick={() => setPortal(true)}
                  >
                    意见反馈
                  </button>
                </>
              ) : null}
            </div>
            <FilingFooter />
          </div>
        </div>
      </div>
    </main>
  );

  return <ConfigProvider theme={antdTheme}>{page}</ConfigProvider>;
}
