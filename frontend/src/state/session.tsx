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
  updateProject as apiUpdateProject,
  deleteProject as apiDeleteProject,
  updateConversation as apiUpdateConversation,
  deleteConversation as apiDeleteConversation,
  getConversationHistory,
  getStatus,
  listSkills,
  listModels,
  listConversations,
  listDevUsers,
  listProjects,
} from "../api/client";
import type {
  ConversationSummary,
  DevUser,
  ModelOption,
  Project,
  SkillOption,
  ServiceStatus,
} from "../types/api";
import {
  TENANT_STORAGE_KEY,
  USER_STORAGE_KEY,
  conversationStorageKey,
  modelStorageKey,
  projectStorageKey,
  readStorage,
  writeStorage,
} from "./storage";

/** 顶栏运行状态 pill 的取值。 */
export type RunStatus =
  | "starting"
  | "ready"
  | "selecting_tools"
  | "thinking"
  | "responding"
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
  conversationsLoading: boolean;
  conversationsLoadFailed: boolean;
  recents: ConversationSummary[];
  modelOptions: ModelOption[];
  selectedModelId: string;
  skills: SkillOption[];
  skillsLoading: boolean;
  skillsError: string | null;
  conversationCursor: string | null;
  recentsCursor: string | null;
  userId: string;
  tenantId: string;
  projectId: string;
  conversationId: string | null;
  /** projects 加载成功，用户/租户上下文就绪。 */
  contextReady: boolean;
  /** 有流式请求进行中（聊天流与审批恢复置位）。 */
  busy: boolean;
  /** 正在运行 AI 输出的会话 ID 集合：多会话可并发，切会话不清空。 */
  runningConversationIds: string[];
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
  | { type: "conversationsLoading" }
  | { type: "conversationsLoadFailed" }
  | {
      type: "conversationsLoaded";
      append: boolean;
      items: ConversationSummary[];
      cursor: string | null;
      projectId: string;
    }
  | {
      type: "recentsLoaded";
      append: boolean;
      items: ConversationSummary[];
      cursor: string | null;
    }
  | { type: "conversationsCleared" }
  | {
      type: "modelsLoaded";
      items: ModelOption[];
      selectedModelId: string;
    }
  | { type: "modelsCleared" }
  | { type: "skillsLoading"; loading: boolean }
  | { type: "skillsLoaded"; items: SkillOption[] }
  | { type: "skillsFailed"; error: string }
  | { type: "contextCleared" }
  | { type: "projectSelected"; projectId: string }
  | { type: "recentSelected"; conversationId: string }
  | { type: "conversationSelected"; conversationId: string | null }
  | { type: "userSwitched"; userId: string; tenantId: string; resetContext: boolean }
  | { type: "conversationCreated"; conversation: ConversationSummary }
  | { type: "busy"; busy: boolean }
  | { type: "creating"; creating: boolean }
  | { type: "runStatus"; runStatus: RunStatus | null }
  | { type: "runStarted"; conversationId: string }
  | { type: "runFinished"; conversationId: string }
  | { type: "runsCleared" }
  | { type: "epoch" }
  | { type: "bootstrapped" };

const INITIAL_STATE: SessionState = {
  status: null,
  bootstrapped: false,
  users: [],
  projects: [],
  conversations: [],
  conversationsLoading: false,
  conversationsLoadFailed: false,
  recents: [],
  modelOptions: [],
  selectedModelId: "",
  skills: [],
  skillsLoading: false,
  skillsError: null,
  conversationCursor: null,
  recentsCursor: null,
  userId: readStorage(USER_STORAGE_KEY) ?? "",
  tenantId: readStorage(TENANT_STORAGE_KEY) ?? "",
  projectId: "",
  conversationId: null,
  contextReady: false,
  busy: false,
  runningConversationIds: [],
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
        conversationsLoading: state.contextReady && state.projectId === action.projectId
          ? state.conversationsLoading
          : Boolean(action.projectId),
        conversationsLoadFailed: state.contextReady && state.projectId === action.projectId
          ? state.conversationsLoadFailed
          : false,
        contextReady: true,
      };
    case "conversationsLoading":
      return { ...state, conversationsLoading: true, conversationsLoadFailed: false };
    case "conversationsLoadFailed":
      return { ...state, conversationsLoading: false, conversationsLoadFailed: true };
    case "conversationsLoaded": {
      const items = action.append
        ? [...state.conversations, ...action.items]
        : action.items;
      return {
        ...state,
        conversations: items,
        conversationsLoading: false,
        conversationsLoadFailed: false,
        conversationCursor: action.cursor,
        ...(action.projectId
          ? {}
          : { recents: items, recentsCursor: action.cursor }),
      };
    }
    case "recentsLoaded": {
      const recents = action.append
        ? [...state.recents, ...action.items]
        : action.items;
      return {
        ...state,
        recents,
        recentsCursor: action.cursor,
        ...(state.projectId
          ? {}
          : { conversations: recents, conversationCursor: action.cursor }),
      };
    }
    case "conversationsCleared":
      return {
        ...state,
        conversations: [],
        conversationsLoading: false,
        conversationsLoadFailed: false,
        conversationCursor: null,
        conversationId: null,
      };
    case "modelsLoaded":
      return {
        ...state,
        modelOptions: action.items,
        selectedModelId: action.selectedModelId,
      };
    case "modelsCleared":
      return { ...state, modelOptions: [], selectedModelId: "" };
    case "skillsLoading":
      return { ...state, skillsLoading: action.loading };
    case "skillsLoaded":
      return {
        ...state,
        skills: action.items,
        skillsLoading: false,
        skillsError: null,
      };
    case "skillsFailed":
      return {
        ...state,
        skills: [],
        skillsLoading: false,
        skillsError: action.error,
      };
    case "contextCleared":
      // 关闭项目：清空项目与会话选择（contextReady 不变，对齐旧 closeProject）。
      return {
        ...state,
        projectId: "",
        conversations: [],
        conversationsLoading: false,
        conversationsLoadFailed: false,
        conversationCursor: null,
        conversationId: null,
      };
    case "projectSelected":
      return { ...state, projectId: action.projectId, conversations: [], conversationCursor: null, conversationsLoading: Boolean(action.projectId), conversationsLoadFailed: false };
    case "recentSelected":
      return {
        ...state,
        projectId: "",
        conversations: state.recents,
        conversationsLoading: false,
        conversationsLoadFailed: false,
        conversationCursor: state.recentsCursor,
        conversationId: action.conversationId,
      };
    case "conversationSelected":
      return { ...state, conversationId: action.conversationId };
    case "userSwitched":
      return {
        ...state,
        userId: action.userId,
        tenantId: action.tenantId,
        contextReady: false,
        ...(action.resetContext
          ? {
              projectId: "",
              conversationId: null,
              recents: [],
              recentsCursor: null,
              modelOptions: [],
              selectedModelId: "",
            }
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
    case "runStarted":
      return state.runningConversationIds.includes(action.conversationId)
        ? state
        : {
            ...state,
            runningConversationIds: [...state.runningConversationIds, action.conversationId],
          };
    case "runFinished":
      return state.runningConversationIds.includes(action.conversationId)
        ? {
            ...state,
            runningConversationIds: state.runningConversationIds.filter(
              (id) => id !== action.conversationId,
            ),
          }
        : state;
    case "runsCleared":
      return state.runningConversationIds.length === 0
        ? state
        : { ...state, runningConversationIds: [] };
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
  openRecent: (conversationId: string) => void;
  closeProject: () => void;
  createProject: (name: string) => Promise<Project | null>;
  updateProject: (id: string, changes: { name?: string; isPinned?: boolean }) => Promise<boolean>;
  deleteProject: (id: string) => Promise<boolean>;
  updateConversation: (id: string, changes: { name?: string; isPinned?: boolean }) => Promise<boolean>;
  deleteConversation: (id: string) => Promise<boolean>;
  selectConversation: (conversationId: string) => void;
  loadMoreConversations: () => Promise<void>;
  loadMoreRecents: () => Promise<void>;
  refreshConversations: () => Promise<void>;
  selectModel: (modelId: string) => void;
  newConversation: (projectId?: string | null) => Promise<ConversationSummary | null>;
  setBusy: (busy: boolean) => void;
  setRunStatus: (runStatus: RunStatus | null) => void;
  /**
   * 聊天流注册当前 AbortController；切换用户/项目时中止，切换会话时保持连接。
   *
   * 多会话可并发：按 conversationId 分开持有，切会话只保留不断开。
   */
  attachStream: (controller: AbortController | null, conversationId?: string | null) => void;
  /** 标记某会话开始 AI 输出（左栏转圈 + 全局 busy 置位）。 */
  markConversationRunning: (conversationId: string) => void;
  /** 标记某会话输出结束（只清该会话，不影响其他并发会话）。 */
  markConversationIdle: (conversationId: string) => void;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const { message } = AntdApp.useApp();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  // generation 与最新 state 通过 ref 供异步操作读取与过期校验（对齐旧实现）。
  const generationRef = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const dataControllerRef = useRef<AbortController | null>(null);
  const streamControllersRef = useRef(new Map<string, AbortController>());
  /** 在途创建未完成时到达的新建请求：只保留最新一次，按序补建，保证每次点击都不丢失。 */
  const pendingNewConversationRef = useRef<{ targetProjectId: string | null } | null>(null);

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

  const markConversationRunning = useCallback((conversationId: string) => {
    if (!conversationId) return;
    dispatchSync({ type: "runStarted", conversationId });
    dispatchSync({ type: "busy", busy: true });
  }, [dispatchSync]);

  const markConversationIdle = useCallback((conversationId: string) => {
    if (!conversationId) return;
    dispatchSync({ type: "runFinished", conversationId });
    const remaining = stateRef.current.runningConversationIds.filter(
      (id) => id !== conversationId,
    );
    if (remaining.length === 0) {
      dispatchSync({ type: "busy", busy: false });
      dispatchSync({ type: "runStatus", runStatus: null });
    }
  }, [dispatchSync]);

  const abortActiveRequests = useCallback(() => {
    for (const controller of streamControllersRef.current.values()) {
      controller.abort();
    }
    streamControllersRef.current.clear();
    dataControllerRef.current?.abort();
    dataControllerRef.current = null;
    dispatchSync({ type: "runsCleared" });
    dispatchSync({ type: "busy", busy: false });
    dispatchSync({ type: "runStatus", runStatus: null });
  }, [dispatchSync]);

  const abortActiveDataRequests = useCallback(() => {
    dataControllerRef.current?.abort();
    dataControllerRef.current = null;
  }, []);

  /** 选中会话：历史消息由聊天视图在 conversationId 变化时加载。 */
  const selectConversationInternal = useCallback((conversationId: string) => {
    if (!conversationId || conversationId === stateRef.current.conversationId) return;
    abortActiveDataRequests();
    bumpGeneration();
    const { userId } = stateRef.current;
    writeStorage(conversationStorageKey(userId), conversationId);
    dispatchSync({ type: "conversationSelected", conversationId });
  }, [abortActiveDataRequests, bumpGeneration, dispatchSync]);

  /** 加载会话列表；autoSelect 时恢复记忆会话或选第一项（仅 bootstrap 路径）。 */
  const loadConversationsInternal = useCallback(
    async (options: {
      append?: boolean;
      refreshOnly?: boolean;
      autoSelect?: boolean;
    }) => {
      const { append = false, refreshOnly = false, autoSelect = false } = options;
      const snapshot = stateRef.current;
      const generation = generationRef.current;
      const { userId, tenantId, projectId } = snapshot;
      const cursor = append ? snapshot.conversationCursor : null;
      const controller = new AbortController();
      dataControllerRef.current = controller;
      if (projectId && !append) dispatchSync({ type: "conversationsLoading" });
      try {
        const data = await listConversations(
          {
            userId,
            tenantId,
            projectId: projectId || null,
            scope: projectId ? undefined : "unassigned",
            limit: 10,
            cursor,
          },
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
          projectId,
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
          dispatchSync({ type: "conversationsLoadFailed" });
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

  const loadRecentsInternal = useCallback(
    async (append = false) => {
      const generation = generationRef.current;
      const { userId, tenantId } = stateRef.current;
      const cursor = append ? stateRef.current.recentsCursor : null;
      try {
        const data = await listConversations({
          userId,
          tenantId,
          scope: "unassigned",
          limit: 10,
          cursor,
        });
        if (!contextMatches(generation, { userId, tenantId })) return;
        dispatchSync({
          type: "recentsLoaded",
          append,
          items: data.items,
          cursor: data.next_cursor,
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (contextMatches(generation, { userId, tenantId })) {
          message.error(error instanceof Error ? error.message : String(error));
        }
      }
    },
    [contextMatches, dispatchSync, message],
  );

  const loadModelsInternal = useCallback(async () => {
    const generation = generationRef.current;
    const { userId, tenantId } = stateRef.current;
    try {
      const data = await listModels({ userId, tenantId });
      if (!contextMatches(generation, { userId, tenantId })) return;
      const saved = readStorage(modelStorageKey(userId, tenantId)) ?? "";
      const availableItems = data.items.filter((item) => item.available);
      const selectedModelId = data.items.some(
        (item) => item.id === saved && item.available,
      )
        ? saved
        : data.items.some(
              (item) => item.id === data.default_model_id && item.available,
            )
          ? data.default_model_id
          : availableItems[0]?.id || "";
      writeStorage(modelStorageKey(userId, tenantId), selectedModelId);
      dispatchSync({
        type: "modelsLoaded",
        items: data.items,
        selectedModelId,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (contextMatches(generation, { userId, tenantId })) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    }
  }, [contextMatches, dispatchSync, message]);

  const loadSkillsInternal = useCallback(async () => {
    dispatchSync({ type: "skillsLoading", loading: true });
    try {
      const data = await listSkills();
      dispatchSync({ type: "skillsLoaded", items: data.items });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      dispatchSync({
        type: "skillsFailed",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [dispatchSync]);

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
        await Promise.all([loadModelsInternal(), loadProjectsInternal()]);
        await Promise.all([
          loadConversationsInternal({ append: false, refreshOnly: false }),
          loadRecentsInternal(),
        ]);
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [
      abortActiveRequests,
      bumpGeneration,
      dispatchSync,
      loadConversationsInternal,
      loadModelsInternal,
      loadProjectsInternal,
      loadRecentsInternal,
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
        abortActiveDataRequests();
        bumpGeneration();
        writeStorage(projectStorageKey(snapshot.userId), "");
        dispatchSync({ type: "contextCleared" });
        await loadConversationsInternal({ append: false, refreshOnly: false });
        return;
      }
      abortActiveDataRequests();
      bumpGeneration();
      writeStorage(projectStorageKey(snapshot.userId), projectId);
      dispatchSync({ type: "projectSelected", projectId });
      dispatchSync({ type: "conversationSelected", conversationId: null });
      await loadConversationsInternal({ append: false, refreshOnly: false });
    },
    [abortActiveDataRequests, bumpGeneration, dispatchSync, loadConversationsInternal],
  );

  const closeProject = useCallback(() => {
    const { userId } = stateRef.current;
    abortActiveDataRequests();
    bumpGeneration();
    writeStorage(projectStorageKey(userId), "");
    dispatchSync({ type: "contextCleared" });
    void loadConversationsInternal({ append: false, refreshOnly: false });
  }, [abortActiveDataRequests, bumpGeneration, dispatchSync, loadConversationsInternal]);

  const openRecent = useCallback(
    (conversationId: string) => {
      const { userId } = stateRef.current;
      abortActiveDataRequests();
      bumpGeneration();
      writeStorage(projectStorageKey(userId), "");
      writeStorage(conversationStorageKey(userId), conversationId);
      dispatchSync({ type: "recentSelected", conversationId });
    },
    [abortActiveDataRequests, bumpGeneration, dispatchSync],
  );

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

  const updateProject = useCallback(async (id: string, changes: { name?: string; isPinned?: boolean }) => {
    const { userId, tenantId } = stateRef.current;
    try {
      await apiUpdateProject(id, { userId, tenantId, ...changes });
      await loadProjectsInternal();
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [loadProjectsInternal, message]);

  const deleteProject = useCallback(async (id: string) => {
    const { userId, tenantId, projectId } = stateRef.current;
    try {
      await apiDeleteProject(id, { userId, tenantId });
      if (projectId === id) {
        abortActiveDataRequests();
        bumpGeneration();
        writeStorage(projectStorageKey(userId), "");
        writeStorage(conversationStorageKey(userId), "");
        dispatchSync({ type: "contextCleared" });
      }
      await loadProjectsInternal();
      if (projectId === id) await loadConversationsInternal({ refreshOnly: true });
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [abortActiveDataRequests, bumpGeneration, dispatchSync, loadConversationsInternal, loadProjectsInternal, message]);

  const updateConversation = useCallback(async (id: string, changes: { name?: string; isPinned?: boolean }) => {
    const { userId, tenantId } = stateRef.current;
    try {
      await apiUpdateConversation(id, { userId, tenantId, ...changes });
      await Promise.all([
        loadConversationsInternal({ refreshOnly: true }),
        loadRecentsInternal(),
      ]);
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [loadConversationsInternal, loadRecentsInternal, message]);

  const deleteConversation = useCallback(async (id: string) => {
    const { userId, tenantId, conversationId } = stateRef.current;
    try {
      await apiDeleteConversation(id, { userId, tenantId });
      if (conversationId === id) {
        bumpGeneration();
        writeStorage(conversationStorageKey(userId), "");
        dispatchSync({ type: "conversationSelected", conversationId: null });
      }
      await Promise.all([
        loadConversationsInternal({ refreshOnly: true }),
        loadRecentsInternal(),
      ]);
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [bumpGeneration, dispatchSync, loadConversationsInternal, loadRecentsInternal, message]);

  const selectConversation = useCallback(
    (conversationId: string) => {
      selectConversationInternal(conversationId);
    },
    [selectConversationInternal],
  );

  const loadMoreConversations = useCallback(async () => {
    await loadConversationsInternal({ append: true, refreshOnly: false });
  }, [loadConversationsInternal]);

  const loadMoreRecents = useCallback(async () => {
    await loadRecentsInternal(true);
  }, [loadRecentsInternal]);

  const refreshConversations = useCallback(async () => {
    await loadConversationsInternal({ append: false, refreshOnly: true });
  }, [loadConversationsInternal]);

  /** 创建普通会话；显式传 projectId 时才在项目中创建。
   *
   * 单次创建的完整实现（创建 + 列表刷新）。并发保护由外层 newConversation
   * 负责：这里假设调用时没有其他创建在途，串行执行，不与自身并发。
   */
  const createConversationInternal = useCallback(async (targetProjectId?: string | null) => {
    const snapshot = stateRef.current;
    if (snapshot.conversationCreating) return null;
    if (!snapshot.contextReady || snapshot.status?.status !== "ready") {
      message.error("服务仍在准备中，请稍候再试。");
      return null;
    }
    const projectId = targetProjectId ?? "";
    // 已停在一张“白纸”上时不再建：当前会话无任何消息、没在跑输出、且目标
    // 作用域一致（同为普通或同一个项目），直接复用当前会话。跨作用域
    // （如项目内点全局新建）仍必须新建，不能把项目会话当成普通会话用。
    // 历史以服务端为准；查不到或检查期间上下文变化时一律按“非空”处理、走新建。
    if (
      projectId === snapshot.projectId &&
      snapshot.conversationId &&
      !snapshot.runningConversationIds.includes(snapshot.conversationId)
    ) {
      const reuseId = snapshot.conversationId;
      const { userId, tenantId } = snapshot;
      try {
        const history = await getConversationHistory({
          conversationId: reuseId,
          userId,
          tenantId,
          limit: 1,
        });
        const current = stateRef.current;
        if (
          history.items.length === 0 &&
          current.conversationId === reuseId &&
          current.userId === userId &&
          current.tenantId === tenantId &&
          current.projectId === projectId &&
          !current.runningConversationIds.includes(reuseId)
        ) {
          return { id: reuseId, project_id: projectId || null };
        }
      } catch {
        // 检查失败不断新建：宁可多一张白纸，不吞掉用户的新建意图。
      }
    }
    if (projectId && !snapshot.projects.some((p) => p.id === projectId)) return null;
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
        projectId: projectId || null,
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

  /**
   * 新建会话入口（侧栏按钮、快捷键、发送兜底共用）。
   *
   * 上一次创建（含列表刷新）未完成时到达的请求不再静默丢弃，而是记下最新
   * 的目标并在当前创建结束后按序补建：新建空会话前后都是相同的欢迎页，
   * 用户无法分辨是否生效，会下意识再点一次，吞掉这次点击就是“点了没创建”。
   * 排队期间到达的多次请求合并为一次，以最终目标为准；返回本次调用自己那
   * 次创建的结果，排队补建的归属由后续列表刷新体现。
   */
  const newConversation = useCallback(async (targetProjectId?: string | null) => {
    if (stateRef.current.conversationCreating) {
      pendingNewConversationRef.current = { targetProjectId: targetProjectId ?? null };
      return null;
    }
    const own = await createConversationInternal(targetProjectId);
    for (;;) {
      const pending = pendingNewConversationRef.current;
      if (pending === null) break;
      pendingNewConversationRef.current = null;
      await createConversationInternal(pending.targetProjectId);
    }
    return own;
  }, [createConversationInternal]);

  const setBusy = useCallback((busy: boolean) => {
    dispatchSync({ type: "busy", busy });
  }, [dispatchSync]);

  const setRunStatus = useCallback((runStatus: RunStatus | null) => {
    dispatchSync({ type: "runStatus", runStatus });
  }, [dispatchSync]);

  const selectModel = useCallback((modelId: string) => {
    const snapshot = stateRef.current;
    const option = snapshot.modelOptions.find((item) => item.id === modelId);
    if (!option || !option.available) return;
    writeStorage(modelStorageKey(snapshot.userId, snapshot.tenantId), modelId);
    dispatchSync({
      type: "modelsLoaded",
      items: snapshot.modelOptions,
      selectedModelId: modelId,
    });
  }, [dispatchSync]);

  const attachStream = useCallback((controller: AbortController | null, conversationId?: string | null) => {
    const key = conversationId ?? stateRef.current.conversationId ?? "";
    if (controller) {
      if (key) streamControllersRef.current.set(key, controller);
      return;
    }
    if (key) {
      streamControllersRef.current.delete(key);
      return;
    }
    streamControllersRef.current.clear();
  }, []);

  // 启动引导：轮询 /api/status，ready 后串行 users→projects→conversations。
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const streams = streamControllersRef.current;

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

        await Promise.all([
          loadModelsInternal(),
          loadProjectsInternal(),
          loadSkillsInternal(),
        ]);
        if (cancelled) return;
        await Promise.all([
          loadConversationsInternal({
            append: false,
            refreshOnly: false,
            autoSelect: true,
          }),
          loadRecentsInternal(),
        ]);
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
      for (const controller of streams.values()) {
        controller.abort();
      }
      streams.clear();
      dataControllerRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({
      ...state,
      changeUser,
      openProject,
      openRecent,
      closeProject,
      createProject,
      updateProject,
      deleteProject,
      updateConversation,
      deleteConversation,
      selectConversation,
      loadMoreConversations,
      loadMoreRecents,
      refreshConversations,
      selectModel,
      newConversation,
      setBusy,
      setRunStatus,
      attachStream,
      markConversationRunning,
      markConversationIdle,
    }),
    [
      state,
      changeUser,
      openProject,
      openRecent,
      closeProject,
      createProject,
      updateProject,
      deleteProject,
      updateConversation,
      deleteConversation,
      selectConversation,
      loadMoreConversations,
      loadMoreRecents,
      refreshConversations,
      selectModel,
      newConversation,
      setBusy,
      setRunStatus,
      attachStream,
      markConversationRunning,
      markConversationIdle,
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
