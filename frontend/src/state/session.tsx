import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { App as AntdApp } from "antd";

import {
  createConversation as apiCreateConversation,
  createProject as apiCreateProject,
  getStatus,
  listConversations,
  listDevUsers,
  listProjects,
} from "../api/client";
import type {
  ConversationSummary,
  DevUser,
  Project,
  ServiceStatus,
} from "../types/api";
import {
  TENANT_STORAGE_KEY,
  USER_STORAGE_KEY,
  conversationStorageKey,
  projectStorageKey,
  readStorage,
  writeStorage,
} from "./storage";

/** 顶栏运行状态 pill 的取值。 */
export type RunStatus =
  | "starting"
  | "ready"
  | "selecting_tools"
  | "processing"
  | "waiting"
  | "failed";

export interface SessionState {
  /** GET /api/status 的最近一次结果；null 表示尚未取得。 */
  status: ServiceStatus | null;
  /** 首次 ready 后完成 users→projects→conversations 引导。 */
  bootstrapped: boolean;
  users: DevUser[];
  projects: Project[];
  conversations: ConversationSummary[];
  conversationCursor: string | null;
  userId: string;
  tenantId: string;
  projectId: string;
  conversationId: string | null;
  /** projects 加载成功，用户/租户上下文就绪。 */
  contextReady: boolean;
  /** 有流式请求进行中（聊天流与审批恢复置位）。 */
  busy: boolean;
  conversationCreating: boolean;
  /** 显式覆盖运行状态（审批等待等场景）；null 时按规则推导。 */
  runStatus: RunStatus | null;
  /** 上下文代数：任何上下文切换（切项目/会话/用户）时递增，供聊天流做过期校验。 */
  epoch: number;
}

type SessionAction =
  | { type: "status"; status: ServiceStatus }
  | {
      type: "bootstrapUsers";
      users: DevUser[];
      userId: string;
      tenantId: string;
    }
  | { type: "bootstrapProjects"; projects: Project[]; projectId: string }
  | {
      type: "conversationsLoaded";
      append: boolean;
      items: ConversationSummary[];
      cursor: string | null;
    }
  | { type: "conversationsCleared" }
  | { type: "contextCleared" }
  | { type: "projectSelected"; projectId: string }
  | { type: "conversationSelected"; conversationId: string | null }
  | { type: "userSwitched"; userId: string; tenantId: string; resetContext: boolean }
  | { type: "conversationCreated"; conversation: ConversationSummary }
  | { type: "busy"; busy: boolean }
  | { type: "creating"; creating: boolean }
  | { type: "runStatus"; runStatus: RunStatus | null }
  | { type: "epoch" }
  | { type: "bootstrapped" };

const INITIAL_STATE: SessionState = {
  status: null,
  bootstrapped: false,
  users: [],
  projects: [],
  conversations: [],
  conversationCursor: null,
  userId: readStorage(USER_STORAGE_KEY) ?? "",
  tenantId: readStorage(TENANT_STORAGE_KEY) ?? "",
  projectId: "",
  conversationId: null,
  contextReady: false,
  busy: false,
  conversationCreating: false,
  runStatus: null,
  epoch: 0,
};

function reducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "status":
      return { ...state, status: action.status };
    case "bootstrapUsers":
      return {
        ...state,
        users: action.users,
        userId: action.userId,
        tenantId: action.tenantId,
      };
    case "bootstrapProjects":
      return {
        ...state,
        projects: action.projects,
        projectId: action.projectId,
        contextReady: true,
      };
    case "conversationsLoaded": {
      const items = action.append
        ? [...state.conversations, ...action.items]
        : action.items;
      return { ...state, conversations: items, conversationCursor: action.cursor };
    }
    case "conversationsCleared":
      return {
        ...state,
        conversations: [],
        conversationCursor: null,
        conversationId: null,
      };
    case "contextCleared":
      // 关闭项目：清空项目与会话选择（contextReady 不变，对齐旧 closeProject）。
      return {
        ...state,
        projectId: "",
        conversations: [],
        conversationCursor: null,
        conversationId: null,
      };
    case "projectSelected":
      return { ...state, projectId: action.projectId };
    case "conversationSelected":
      return { ...state, conversationId: action.conversationId };
    case "userSwitched":
      return {
        ...state,
        userId: action.userId,
        tenantId: action.tenantId,
        contextReady: false,
        ...(action.resetContext
          ? { projectId: "", conversationId: null }
          : {}),
      };
    case "conversationCreated":
      return {
        ...state,
        conversationId: action.conversation.id,
        conversationCreating: false,
      };
    case "busy":
      return { ...state, busy: action.busy };
    case "creating":
      return { ...state, conversationCreating: action.creating };
    case "runStatus":
      return { ...state, runStatus: action.runStatus };
    case "epoch":
      return { ...state, epoch: state.epoch + 1 };
    case "bootstrapped":
      return { ...state, bootstrapped: true };
    default:
      return state;
  }
}

/** 顶栏运行 pill 的推导规则（与旧 setRunStatus/setStatus 联动语义一致）。 */
export function deriveRunStatus(state: SessionState): RunStatus {
  if (state.status?.status === "error") return "failed";
  if (state.runStatus) return state.runStatus;
  if (state.busy || state.conversationCreating) return "processing";
  if (!state.status || state.status.status === "starting") return "starting";
  return "ready";
}

function tenantCandidates(user: DevUser | null): string[] {
  if (!user) return [];
  if (user.tenant_ids.length > 0) return [...user.tenant_ids];
  return user.tenant_id ? [user.tenant_id] : [];
}

const SessionContext = createContext<SessionContextValue | null>(null);

export interface SessionContextValue extends SessionState {
  changeUser: (userId: string) => Promise<void>;
  openProject: (projectId: string) => Promise<void>;
  closeProject: () => void;
  createProject: (name: string) => Promise<Project | null>;
  selectConversation: (conversationId: string) => void;
  loadMoreConversations: () => Promise<void>;
  refreshConversations: () => Promise<void>;
  newConversation: () => Promise<ConversationSummary | null>;
  setBusy: (busy: boolean) => void;
  setRunStatus: (runStatus: RunStatus | null) => void;
  /** 聊天流注册当前 AbortController，使切换上下文时能中止流。 */
  attachStream: (controller: AbortController | null) => void;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const { message } = AntdApp.useApp();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  // generation 与最新 state 通过 ref 供异步操作读取与过期校验（对齐旧实现）。
  const generationRef = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const dataControllerRef = useRef<AbortController | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);

  // dispatch 的同时同步 stateRef，保证异步操作立即读到最新上下文
  // （React 的重渲染是异步的，直接读 stateRef 会拿到过期值）。
  const dispatchSync = useCallback((action: SessionAction) => {
    stateRef.current = reducer(stateRef.current, action);
    dispatch(action);
  }, []);

  const bumpGeneration = useCallback(() => {
    generationRef.current += 1;
    dispatchSync({ type: "epoch" });
    return generationRef.current;
  }, [dispatchSync]);

  const contextMatches = useCallback(
    (generation: number, ctx: {
      userId?: string;
      tenantId?: string;
      projectId?: string;
      conversationId?: string | null;
    }): boolean => {
      const current = stateRef.current;
      if (generation !== generationRef.current) return false;
      if (ctx.userId !== undefined && ctx.userId !== current.userId) return false;
      if (ctx.tenantId !== undefined && ctx.tenantId !== current.tenantId) return false;
      if (ctx.projectId !== undefined && ctx.projectId !== current.projectId) return false;
      if (
        ctx.conversationId !== undefined &&
        ctx.conversationId !== current.conversationId
      ) {
        return false;
      }
      return true;
    },
    [],
  );

  const abortActiveRequests = useCallback(() => {
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    dataControllerRef.current?.abort();
    dataControllerRef.current = null;
    dispatchSync({ type: "busy", busy: false });
    dispatchSync({ type: "runStatus", runStatus: null });
  }, [dispatchSync]);

  /** 选中会话：历史消息由聊天视图在 conversationId 变化时加载。 */
  const selectConversationInternal = useCallback((conversationId: string) => {
    if (!conversationId || conversationId === stateRef.current.conversationId) return;
    abortActiveRequests();
    bumpGeneration();
    const { userId } = stateRef.current;
    writeStorage(conversationStorageKey(userId), conversationId);
    dispatchSync({ type: "conversationSelected", conversationId });
  }, [abortActiveRequests, bumpGeneration, dispatchSync]);

  /** 加载会话列表；autoSelect 时恢复记忆会话或选第一项（仅 bootstrap 路径）。 */
  const loadConversationsInternal = useCallback(
    async (options: {
      append?: boolean;
      refreshOnly?: boolean;
      autoSelect?: boolean;
    }) => {
      const { append = false, refreshOnly = false, autoSelect = false } = options;
      const snapshot = stateRef.current;
      if (!snapshot.projectId) {
        dispatchSync({ type: "conversationsCleared" });
        return;
      }
      const generation = generationRef.current;
      const { userId, tenantId, projectId } = snapshot;
      const cursor = append ? snapshot.conversationCursor : null;
      const controller = new AbortController();
      dataControllerRef.current = controller;
      try {
        const data = await listConversations(
          { userId, tenantId, projectId, limit: 20, cursor },
          controller.signal,
        );
        if (
          !contextMatches(generation, { userId, tenantId, projectId })
        ) {
          return;
        }
        dispatchSync({
          type: "conversationsLoaded",
          append,
          items: data.items,
          cursor: data.next_cursor,
        });
        if (autoSelect && !append && !refreshOnly) {
          const saved = readStorage(conversationStorageKey(userId));
          const target =
            data.items.find((item) => item.id === saved) ?? data.items[0];
          if (target) {
            selectConversationInternal(target.id);
          } else {
            dispatchSync({ type: "conversationSelected", conversationId: null });
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (contextMatches(generation, { userId, tenantId, projectId })) {
          message.error(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (dataControllerRef.current === controller) {
          dataControllerRef.current = null;
        }
      }
    },
    [contextMatches, dispatchSync, message, selectConversationInternal],
  );

  const loadProjectsInternal = useCallback(async () => {
    const generation = generationRef.current;
    const { userId, tenantId } = stateRef.current;
    try {
      const data = await listProjects({ userId, tenantId });
      if (!contextMatches(generation, { userId, tenantId })) return;
      const saved = readStorage(projectStorageKey(userId)) ?? "";
      // 记忆项目失效时不做 is_default 回退，留空由发消息兜底。
      const projectId = data.items.some((project) => project.id === saved)
        ? saved
        : "";
      writeStorage(projectStorageKey(userId), projectId);
      dispatchSync({
        type: "bootstrapProjects",
        projects: data.items,
        projectId,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      throw error;
    }
  }, [contextMatches, dispatchSync]);

  const changeUser = useCallback(
    async (userId: string) => {
      const snapshot = stateRef.current;
      abortActiveRequests();
      bumpGeneration();
      const selected =
        snapshot.users.find((user) => user.user_id === userId) ?? null;
      const candidates = tenantCandidates(selected);
      const previousTenantId = snapshot.tenantId;
      const tenantId = candidates.includes(previousTenantId)
        ? previousTenantId
        : selected?.default_tenant_id || candidates[0] || "";
      const tenantChanged = userId !== snapshot.userId || tenantId !== previousTenantId;
      writeStorage(USER_STORAGE_KEY, userId);
      writeStorage(TENANT_STORAGE_KEY, tenantId);
      dispatchSync({ type: "userSwitched", userId, tenantId, resetContext: tenantChanged });
      try {
        await loadProjectsInternal();
        await loadConversationsInternal({ append: false, refreshOnly: false });
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [
      abortActiveRequests,
      bumpGeneration,
      dispatchSync,
      loadConversationsInternal,
      loadProjectsInternal,
      message,
    ],
  );

  const openProject = useCallback(
    async (projectId: string) => {
      const snapshot = stateRef.current;
      if (!snapshot.projects.some((project) => project.id === projectId)) {
        return;
      }
      if (snapshot.projectId === projectId) {
      // 再次点击当前项目 = 关闭项目。
        abortActiveRequests();
        bumpGeneration();
        writeStorage(projectStorageKey(snapshot.userId), "");
        dispatchSync({ type: "contextCleared" });
        return;
      }
      abortActiveRequests();
      bumpGeneration();
      writeStorage(projectStorageKey(snapshot.userId), projectId);
      dispatchSync({ type: "projectSelected", projectId });
      await loadConversationsInternal({ append: false, refreshOnly: false });
    },
    [abortActiveRequests, bumpGeneration, dispatchSync, loadConversationsInternal],
  );

  const closeProject = useCallback(() => {
    const { userId } = stateRef.current;
    abortActiveRequests();
    bumpGeneration();
    writeStorage(projectStorageKey(userId), "");
    dispatchSync({ type: "contextCleared" });
  }, [abortActiveRequests, bumpGeneration, dispatchSync]);

  /** 新增项目：创建成功后刷新列表并打开新项目。 */
  const createProject = useCallback(
    async (name: string): Promise<Project | null> => {
      const snapshot = stateRef.current;
      if (snapshot.status?.status !== "ready" || !snapshot.contextReady) {
        message.error("服务仍在准备中，请稍候再试。");
        return null;
      }
      const cleanName = name.trim();
      if (!cleanName) {
        message.error("项目名称不能为空。");
        return null;
      }
      const generation = generationRef.current;
      const { userId, tenantId } = snapshot;
      try {
        const project = await apiCreateProject({
          userId,
          tenantId,
          name: cleanName,
        });
        if (!contextMatches(generation, { userId, tenantId })) return null;
        await loadProjectsInternal();
        if (!contextMatches(generation, { userId, tenantId })) return null;
        await openProject(project.id);
        return project;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return null;
        }
        message.error(error instanceof Error ? error.message : String(error));
        return null;
      }
    },
    [contextMatches, loadProjectsInternal, message, openProject],
  );

  const selectConversation = useCallback(
    (conversationId: string) => {
      selectConversationInternal(conversationId);
    },
    [selectConversationInternal],
  );

  const loadMoreConversations = useCallback(async () => {
    await loadConversationsInternal({ append: true, refreshOnly: false });
  }, [loadConversationsInternal]);

  const refreshConversations = useCallback(async () => {
    await loadConversationsInternal({ append: false, refreshOnly: true });
  }, [loadConversationsInternal]);

  /** 创建会话：守卫链与默认项目兜底对齐旧 createConversation。 */
  const newConversation = useCallback(async () => {
    const snapshot = stateRef.current;
    if (snapshot.busy || snapshot.conversationCreating) return null;
    if (
      !snapshot.contextReady ||
      snapshot.status?.status !== "ready" ||
      snapshot.projects.length === 0
    ) {
      message.error("服务仍在准备中，请稍候再试。");
      return null;
    }
    let projectId = snapshot.projectId;
    if (!projectId || !snapshot.projects.some((p) => p.id === projectId)) {
      projectId =
        snapshot.projects.find((project) => project.is_default)?.id ??
        snapshot.projects[0]?.id ??
        "";
    }
    if (!projectId) {
      message.error("没有可用项目，请先创建项目。");
      return null;
    }
    const generation = bumpGeneration();
    const { userId, tenantId } = snapshot;
    if (projectId !== snapshot.projectId) {
      writeStorage(projectStorageKey(userId), projectId);
      dispatchSync({ type: "projectSelected", projectId });
    }
    dispatchSync({ type: "creating", creating: true });
    try {
      const conversation = await apiCreateConversation({
        userId,
        tenantId,
        projectId,
      });
      if (!contextMatches(generation, { userId, tenantId, projectId })) {
        return null;
      }
      writeStorage(conversationStorageKey(userId), conversation.id);
      bumpGeneration();
      dispatchSync({ type: "conversationCreated", conversation });
      await loadConversationsInternal({ append: false, refreshOnly: true });
      return conversation;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return null;
      }
      message.error(
        error instanceof Error
          ? error.message
          : "会话创建失败，输入内容已保留。",
      );
      return null;
    } finally {
      dispatchSync({ type: "creating", creating: false });
    }
  }, [
    bumpGeneration,
    contextMatches,
    dispatchSync,
    loadConversationsInternal,
    message,
  ]);

  const setBusy = useCallback((busy: boolean) => {
    dispatchSync({ type: "busy", busy });
  }, [dispatchSync]);

  const setRunStatus = useCallback((runStatus: RunStatus | null) => {
    dispatchSync({ type: "runStatus", runStatus });
  }, [dispatchSync]);

  const attachStream = useCallback((controller: AbortController | null) => {
    streamControllerRef.current = controller;
  }, []);

  // 启动引导：轮询 /api/status，ready 后串行 users→projects→conversations。
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const fail = (err: unknown) => {
      const text = err instanceof Error ? err.message : String(err);
      dispatchSync({
        type: "status",
        status: {
          status: "error",
          message: text,
          provider: "",
          model: "",
          mcp_servers: [],
          skills: [],
          database: "",
          memory_store: "",
        },
      });
    };

    const bootstrap = async () => {
      try {
        const usersData = await listDevUsers();
        if (cancelled) return;
        const users = usersData.items;
        const savedUserId = stateRef.current.userId;
        const selected =
          users.find((user) => user.user_id === savedUserId) ??
          users.find((user) => user.is_default) ??
          users[0] ??
          null;
        const userId = selected?.user_id ?? "";
        const candidates = tenantCandidates(selected);
        let tenantId = stateRef.current.tenantId;
        if (!candidates.includes(tenantId)) {
          tenantId = selected?.default_tenant_id || candidates[0] || "";
        }
        writeStorage(USER_STORAGE_KEY, userId);
        writeStorage(TENANT_STORAGE_KEY, tenantId);
        dispatchSync({ type: "bootstrapUsers", users, userId, tenantId });

        await loadProjectsInternal();
        if (cancelled) return;
        await loadConversationsInternal({
          append: false,
          refreshOnly: false,
          autoSelect: true,
        });
        if (cancelled) return;
        dispatchSync({ type: "bootstrapped" });
      } catch (error) {
        if (!cancelled) fail(error);
      }
    };

    const poll = async () => {
      try {
        const status = await getStatus();
        if (cancelled) return;
        dispatchSync({ type: "status", status });
        if (status.status === "ready") {
          await bootstrap();
          return;
        }
        if (status.status === "starting") {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (error) {
        // 状态接口失败进入 error 态，不再轮询。
        if (!cancelled) fail(error);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      streamControllerRef.current?.abort();
      dataControllerRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({
      ...state,
      changeUser,
      openProject,
      closeProject,
      createProject,
      selectConversation,
      loadMoreConversations,
      refreshConversations,
      newConversation,
      setBusy,
      setRunStatus,
      attachStream,
    }),
    [
      state,
      changeUser,
      openProject,
      closeProject,
      createProject,
      selectConversation,
      loadMoreConversations,
      refreshConversations,
      newConversation,
      setBusy,
      setRunStatus,
      attachStream,
    ],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession 必须在 SessionProvider 内使用。");
  }
  return context;
}
