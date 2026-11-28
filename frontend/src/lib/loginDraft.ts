export type LoginMode = "password" | "sms" | "register" | "reset";

export interface LoginDraft {
  mode: LoginMode;
  login_identifier: string;
  login_secret: string;
  confirm_secret: string;
  phone_number: string;
  verify_code: string;
  challengeId: string;
  waitUntil: number;
}

export interface BindDraft {
  password: string;
  phone_number: string;
  verify_code: string;
  challengeId: string;
  waitUntil: number;
}

const LOGIN_KEY = "melonclaw.login-draft.v1";
const BIND_KEY = "melonclaw.bind-draft.v1";

function readJson(key: string): unknown {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function readLoginDraft(): LoginDraft | null {
  const data = readJson(LOGIN_KEY);
  if (!data || typeof data !== "object") return null;
  const draft = data as Partial<LoginDraft>;
  if (draft.mode !== "password" && draft.mode !== "sms" && draft.mode !== "register" && draft.mode !== "reset") return null;
  return {
    mode: draft.mode,
    login_identifier: text(draft.login_identifier),
    login_secret: text(draft.login_secret),
    confirm_secret: text(draft.confirm_secret),
    phone_number: text(draft.phone_number),
    verify_code: text(draft.verify_code),
    challengeId: text(draft.challengeId),
    waitUntil: typeof draft.waitUntil === "number" ? draft.waitUntil : 0,
  };
}

export function writeLoginDraft(draft: LoginDraft) {
  sessionStorage.setItem(LOGIN_KEY, JSON.stringify(draft));
}

export function clearLoginDraft() {
  sessionStorage.removeItem(LOGIN_KEY);
}

export function readBindDraft(): BindDraft | null {
  const data = readJson(BIND_KEY);
  if (!data || typeof data !== "object") return null;
  const draft = data as Partial<BindDraft>;
  return {
    password: text(draft.password),
    phone_number: text(draft.phone_number),
    verify_code: text(draft.verify_code),
    challengeId: text(draft.challengeId),
    waitUntil: typeof draft.waitUntil === "number" ? draft.waitUntil : 0,
  };
}

export function writeBindDraft(draft: BindDraft) {
  sessionStorage.setItem(BIND_KEY, JSON.stringify(draft));
}

export function clearBindDraft() {
  sessionStorage.removeItem(BIND_KEY);
}

export function remainingSeconds(waitUntil: number): number {
  if (!waitUntil) return 0;
  return Math.max(0, Math.ceil((waitUntil - Date.now()) / 1000));
}
