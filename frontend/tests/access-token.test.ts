import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, uploadAttachment } from "../src/api/client";
import {
  ACCESS_TOKEN_STORAGE,
  AUTH_EVENT,
  AUTH_STORAGE,
  login,
  logout,
  passwordlessLogin,
  switchUser,
} from "../src/api/auth";
import { conversationStorageKey, projectStorageKey } from "../src/state/storage";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("browser access token", () => {
  it("stores login and passwordless tokens, and keeps the current token when switch fails", async () => {
    const events = vi.fn();
    window.addEventListener(AUTH_EVENT, events);
    localStorage.setItem(projectStorageKey("bob"), "project-1");
    localStorage.setItem(conversationStorageKey("bob"), "conversation-1");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/login")) return new Response(JSON.stringify({ access_token: "alice-token" }));
      if (url.endsWith("/api/auth/passwordless")) return new Response(JSON.stringify({ access_token: "admin-token" }));
      if (url.endsWith("/api/auth/switch")) {
        const body = JSON.parse(String(init?.body)) as { user_id: string };
        if (body.user_id === "missing") {
          return new Response(JSON.stringify({ detail: "用户不存在或已删除。" }), { status: 422 });
        }
        return new Response(JSON.stringify({ access_token: "bob-token" }));
      }
      throw new Error(url);
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      await login("alice", "password-123");
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe("alice-token");
      expect(events).toHaveBeenCalledTimes(1);
      await expect(switchUser("missing")).rejects.toMatchObject({ status: 422 });
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe("alice-token");
      expect(localStorage.getItem(projectStorageKey("bob"))).toBe("project-1");
      expect(localStorage.getItem(conversationStorageKey("bob"))).toBe("conversation-1");
      expect(events).toHaveBeenCalledTimes(1);
      await switchUser("bob");
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe("bob-token");
      expect(localStorage.getItem(projectStorageKey("bob"))).toBe("");
      expect(localStorage.getItem(conversationStorageKey("bob"))).toBe("");
      expect(events).toHaveBeenCalledTimes(2);
      const switchCall = fetchMock.mock.calls.find((call) => {
        const init = call[1] as RequestInit | undefined;
        return String(call[0]).endsWith("/api/auth/switch") && String(init?.body).includes("bob");
      });
      expect((switchCall?.[1] as RequestInit | undefined)?.headers).toMatchObject({
        Authorization: "Bearer alice-token",
      });
      await passwordlessLogin();
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe("admin-token");
      const calls = fetchMock.mock.calls.length;
      logout();
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBeNull();
      expect(localStorage.getItem(AUTH_STORAGE)).toBeTruthy();
      expect(fetchMock).toHaveBeenCalledTimes(calls);
      expect(events).toHaveBeenCalledTimes(4);
    } finally {
      window.removeEventListener(AUTH_EVENT, events);
    }
  });

  it.each(["old-token", null])("keeps a newer login when a Fetch request sent with %s receives 401", async (token) => {
    if (token) localStorage.setItem(ACCESS_TOKEN_STORAGE, token);
    let respond!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { respond = resolve; });
    const fetchMock = vi.fn().mockReturnValue(response);
    vi.stubGlobal("fetch", fetchMock);
    const expired = vi.fn();
    window.addEventListener("melonclaw-auth-expired", expired);
    try {
      const pending = apiRequest("/api/protected");
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(token ? `Bearer ${token}` : undefined);
      localStorage.setItem(ACCESS_TOKEN_STORAGE, "new-token");
      respond(new Response(JSON.stringify({ detail: "请先登录。" }), { status: 401 }));
      await expect(pending).rejects.toMatchObject({ status: 401 });
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe("new-token");
      expect(expired).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("melonclaw-auth-expired", expired);
    }
  });

  it.each([false, true])("only clears a matching token on an upload 401 (token replaced: %s)", async (replaced) => {
    localStorage.setItem(ACCESS_TOKEN_STORAGE, "upload-token");
    const headers: Record<string, string> = {};
    const expired = vi.fn();
    window.addEventListener("melonclaw-auth-expired", expired);
    let respond!: () => void;
    class FakeRequest {
      status = 401;
      responseText = JSON.stringify({ detail: "请先登录。" });
      upload = {};
      onload: (() => void) | null = null;
      open() {}
      setRequestHeader(name: string, value: string) { headers[name] = value; }
      send() { respond = () => this.onload?.(); }
      abort() {}
    }
    vi.stubGlobal("XMLHttpRequest", FakeRequest);
    try {
      const file = new File(["a"], "a.txt", { type: "text/plain" });
      const pending = uploadAttachment(
        { projectId: "project" },
        { userId: "alice", file, clientRequestId: "request" },
      );
      expect(headers.Authorization).toBe("Bearer upload-token");
      if (replaced) localStorage.setItem(ACCESS_TOKEN_STORAGE, "new-token");
      respond();
      await expect(pending).rejects.toMatchObject({ status: 401, message: "请先登录。" });
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE)).toBe(replaced ? "new-token" : null);
      expect(expired).toHaveBeenCalledTimes(replaced ? 0 : 1);
    } finally {
      window.removeEventListener("melonclaw-auth-expired", expired);
    }
  });
});
