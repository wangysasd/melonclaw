import { createRequestId } from "../lib/requestId";
import { conversationStorageKey, projectStorageKey, writeStorage } from "../state/storage";
import { apiRequest } from "./client";

export interface AuthUser {
  user_id: string;
  user_name_zh: string;
  tenant_id: string;
  tenant_name_zh: string;
}
export const AUTH_EVENT = "melonclaw-auth-change";
export const AUTH_STORAGE = "melonclaw.auth-change";
export const ACCESS_TOKEN_STORAGE = "melonclaw.access-token";
let currentUser = "";
export function setRequestUser(userId: string) { currentUser = userId; }
export function identityHeaders(): Record<string, string> {
  return currentUser ? { "X-Melonclaw-User": currentUser } : {};
}
export function accessToken(): string {
  try {
    return localStorage.getItem(ACCESS_TOKEN_STORAGE) ?? "";
  } catch {
    return "";
  }
}
export function storeAccessToken(token: string) {
  localStorage.setItem(ACCESS_TOKEN_STORAGE, token);
}
export function clearAccessToken() {
  localStorage.removeItem(ACCESS_TOKEN_STORAGE);
}
export function authHeader(): Record<string, string> {
  const token = accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
export function requestHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { ...authHeader(), ...identityHeaders(), ...extra };
}
export function notifyAuthChange() {
  window.dispatchEvent(new Event(AUTH_EVENT));
  writeStorage(AUTH_STORAGE, createRequestId());
}
export const getAuthSession = () => apiRequest<AuthUser>("/api/auth/session");
export const getAuthConfig = () => apiRequest<{ passwordless: boolean; assistant_name: string }>("/api/auth/config");
export async function login(user_id: string, password: string) {
  const body = await apiRequest<{ access_token: string }>("/api/auth/login", { method: "POST", body: { user_id, password } });
  storeAccessToken(body.access_token);
  notifyAuthChange();
}
export async function passwordlessLogin() {
  const body = await apiRequest<{ access_token: string }>("/api/auth/passwordless", { method: "POST" });
  storeAccessToken(body.access_token);
  notifyAuthChange();
}
export async function switchUser(user_id: string) {
  const body = await apiRequest<{ access_token: string }>("/api/auth/switch", { method: "POST", body: { user_id } });
  storeAccessToken(body.access_token);
  writeStorage(projectStorageKey(user_id), "");
  writeStorage(conversationStorageKey(user_id), "");
  notifyAuthChange();
}
export async function logout() {
  clearAccessToken();
  notifyAuthChange();
}
