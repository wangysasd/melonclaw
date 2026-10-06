import { Alert, Button, Spin } from "antd";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { AUTH_EVENT, AUTH_STORAGE, getAuthSession, setRequestUser, type AuthUser } from "../api/auth";
import { LoginPage } from "./LoginPage";

export function AuthGate({ children }: { children: (user: AuthUser) => ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++sequence.current;
    setChecking(true); setError(""); setUser(null); setRequestUser("");
    try {
      const current = await getAuthSession();
      if (sequence.current !== version) return;
      setRequestUser(current.user_id); setUser(current);
    } catch (err) {
      if (sequence.current !== version) return;
      if (!(err instanceof ApiError && err.status === 401)) setError(err instanceof Error ? err.message : "服务暂不可用");
    } finally { if (sequence.current === version) setChecking(false); }
  }, []);
  useEffect(() => {
    void refresh();
    const changed = () => void refresh();
    const storage = (event: StorageEvent) => { if (event.key === AUTH_STORAGE) changed(); };
    const expired = () => { ++sequence.current; setUser(null); setRequestUser(""); setChecking(false); };
    window.addEventListener(AUTH_EVENT, changed);
    window.addEventListener("storage", storage);
    window.addEventListener("melonclaw-auth-expired", expired);
    window.addEventListener("pageshow", changed);
    return () => {
      // Invalidate pending session reads after unmount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++sequence.current;
      window.removeEventListener(AUTH_EVENT, changed);
      window.removeEventListener("storage", storage);
      window.removeEventListener("melonclaw-auth-expired", expired);
      window.removeEventListener("pageshow", changed);
    };
  }, [refresh]);
  if (checking) return <main className="login-page"><Spin aria-label="正在检查登录" /></main>;
  if (error) return <main className="login-page"><section className="login-card"><Alert type="error" title={error} /><Button onClick={() => void refresh()}>重试</Button></section></main>;
  return user ? children(user) : <LoginPage />;
}
