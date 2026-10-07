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
let currentUser = "";
export function setRequestUser(userId: string) { currentUser = userId; }
export function identityHeaders(): Record<string, string> {
  return currentUser ? { "X-Melonclaw-User": currentUser } : {};
}
export function notifyAuthChange() {
  window.dispatchEvent(new Event(AUTH_EVENT));
  writeStorage(AUTH_STORAGE, createRequestId());
}
export const getAuthSession = () => apiRequest<AuthUser>("/api/auth/session");
export const getAuthConfig = () => apiRequest<{ passwordless: boolean }>("/api/auth/config");
export async function login(user_id: string, password: string) {
  await apiRequest("/api/auth/login", { method: "POST", body: { user_id, password } });
  notifyAuthChange();
}
export async function passwordlessLogin() {
  await apiRequest("/api/auth/passwordless", { method: "POST" });
  notifyAuthChange();
}
export async function switchUser(user_id: string) {
  await apiRequest("/api/auth/switch", { method: "POST", body: { user_id } });
  writeStorage(projectStorageKey(user_id), "");
  writeStorage(conversationStorageKey(user_id), "");
  notifyAuthChange();
}
export async function logout() {
  await apiRequest("/api/auth/logout", { method: "POST" });
  notifyAuthChange();
}
