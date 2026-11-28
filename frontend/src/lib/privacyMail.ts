export const PRIVACY_PATH = "/privacy";
export const PRIVACY_REQUEST_PATH = "/privacy#requests";

const EMAIL = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

/** 只接受可放进 mailto 的公开邮箱。空值或格式不符时不生成链接。 */
export function publicOperatorEmail(value: string | undefined | null): string {
  const email = (value ?? "").trim();
  if (!email || email.length > 254 || !EMAIL.test(email)) return "";
  return email;
}

