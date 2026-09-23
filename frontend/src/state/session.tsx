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
  /** 已建库但尚未发送首条消息的会话；侧栏列表此时仍不展示它。 */
  draftConversationId: string | null;
  /** 首次发送后立即显示，直到服务端列表包含该会话。 */
  optimisticConversations: (ConversationSummary & { localOnly?: boolean })[];
  activeLocalSubmissionId: string | null;
  /** 首条消息落库时递增，通知侧栏刷新展开的非当前项目。 */
  conversationListRevision: number;
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
  | { type: "newDraftStarted"; projectId: string }
  | { type: "projectConversationSelected"; projectId: string; conversationId: string }
  | { type: "recentSelected"; conversationId: string }
  | { type: "conversationSelected"; conversationId: string | null }
  | { type: "userSwitched"; userId: string; tenantId: string; resetContext: boolean }
  | { type: "conversationCreated"; conversation: ConversationSummary }
  | { type: "conversationSubmitted"; conversation: ConversationSummary & { localOnly?: boolean } }
  | { type: "conversationSubmissionIdentified"; localId: string; conversationId: string }
  | { type: "conversationSubmissionFailed"; conversationId: string }
  | { type: "conversationListed"; conversationIds: string[] }
  | { type: "conversationStarted"; conversationId: string }
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
  draftConversationId: null,
  optimisticConversations: [],
  activeLocalSubmissionId: null,
  conversationListRevision: 0,
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
      const listedIds = new Set(action.items.map((item) => item.id));
      return {
        ...state,
        conversations: items,
        optimisticConversations: state.optimisticConversations.filter((item) => !listedIds.has(item.id)),
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
      const listedIds = new Set(action.items.map((item) => item.id));
      return {
        ...state,
        recents,
        optimisticConversations: state.optimisticConversations.filter((item) => !listedIds.has(item.id)),
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
        draftConversationId: null,
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
        draftConversationId: null,
        activeLocalSubmissionId: null,
      };
    case "projectSelected":
      return { ...state, projectId: action.projectId, draftConversationId: null, activeLocalSubmissionId: null, conversations: [], conversationCursor: null, conversationsLoading: Boolean(action.projectId), conversationsLoadFailed: false };
    case "newDraftStarted":
      return {
        ...state,
        projectId: action.projectId,
        conversationId: null,
        draftConversationId: null,
        activeLocalSubmissionId: null,
        conversations: action.projectId === state.projectId ? state.conversations : action.projectId ? [] : state.recents,
        conversationCursor: action.projectId === state.projectId ? state.conversationCursor : action.projectId ? null : state.recentsCursor,
        conversationsLoading: action.projectId === state.projectId ? state.conversationsLoading : Boolean(action.projectId),
        conversationsLoadFailed: action.projectId === state.projectId ? state.conversationsLoadFailed : false,
      };
    case "projectConversationSelected":
      return { ...state, projectId: action.projectId, conversationId: action.conversationId, draftConversationId: null, activeLocalSubmissionId: null, conversations: [], conversationCursor: null, conversationsLoading: true, conversationsLoadFailed: false };
    case "recentSelected":
      return {
        ...state,
        projectId: "",
        conversations: state.recents,
        conversationsLoading: false,
        conversationsLoadFailed: false,
        conversationCursor: state.recentsCursor,
        conversationId: action.conversationId,
        draftConversationId: null,
        activeLocalSubmissionId: null,
      };
    case "conversationSelected":
      return { ...state, conversationId: action.conversationId, draftConversationId: action.conversationId === state.draftConversationId ? state.draftConversationId : null, activeLocalSubmissionId: null };
    case "userSwitched":
      return {
        ...state,
        userId: action.userId,
        tenantId: action.tenantId,
        contextReady: false,
        optimisticConversations: [],
        activeLocalSubmissionId: null,
        ...(action.resetContext
          ? {
              projectId: "",
              conversationId: null,
              draftConversationId: null,
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
        draftConversationId: action.conversation.id,
        activeLocalSubmissionId: null,
        conversationCreating: false,
      };
    case "conversationSubmitted":
      return state.optimisticConversations.some((item) => item.id === action.conversation.id)
        ? state
        : { ...state, optimisticConversations: [action.conversation, ...state.optimisticConversations], activeLocalSubmissionId: action.conversation.localOnly ? action.conversation.id : state.activeLocalSubmissionId };
    case "conversationSubmissionIdentified":
      return {
        ...state,
        optimisticConversations: state.optimisticConversations.map((item) => item.id === action.localId
          ? { ...item, id: action.conversationId, localOnly: false }
          : item),
        activeLocalSubmissionId: state.activeLocalSubmissionId === action.localId ? null : state.activeLocalSubmissionId,
      };
    case "conversationSubmissionFailed":
      return {
        ...state,
        optimisticConversations: state.optimisticConversations.filter((item) => item.id !== action.conversationId),
        activeLocalSubmissionId: state.activeLocalSubmissionId === action.conversationId ? null : state.activeLocalSubmissionId,
      };
    case "conversationListed": {
      const listedIds = new Set(action.conversationIds);
      return {
        ...state,
        optimisticConversations: state.optimisticConversations.filter((item) => !listedIds.has(item.id)),
      };
    }
    case "conversationStarted":
      return {
        ...state,
        draftConversationId: state.draftConversationId === action.conversationId ? null : state.draftConversationId,
        conversationListRevision: state.conversationListRevision + 1,
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
  getCurrentContext: () => Pick<SessionState, "conversationId" | "draftConversationId" | "projectId" | "userId" | "tenantId" | "epoch">;
  changeUser: (userId: string) => Promise<void>;
  openProject: (projectId: string) => Promise<void>;
  openProjectConversation: (projectId: string, conversationId: string) => void;
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
  startNewConversation: (projectId?: string | null) => void;
  ensureConversation: () => Promise<ConversationSummary | null>;
  markConversationSubmitted: (conversationId: string, projectId: string, content: string, localOnly?: boolean) => void;
  identifyConversationSubmission: (localId: string, conversationId: string) => void;
  cancelConversationSubmission: (conversationId: string) => void;
  acknowledgeConversationRows: (conversationIds: string[]) => void;
  markConversationStarted: (conversationId: string, projectId: string) => void;
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
  /** 首次发送与附件入口同时请求时共用一次创建。 */
  const creationPromiseRef = useRef<Promise<ConversationSummary | null> | null>(null);

  // dispatch 的同时同步 stateRef，保证异步操作立即读到最新上下文
  // （React 的重渲染是异步的，直接读 stateRef 会拿到过期值）。
  const dispatchSync = useCallback((action: SessionAction) => {
    stateRef.current = reducer(stateRef.current, action);
    dispatch(action);
  }, []);

  const getCurrentContext = useCallback(() => {
    const { conversationId, draftConversationId, projectId, userId, tenantId, epoch } = stateRef.current;
    return { conversationId, draftConversationId, projectId, userId, tenantId, epoch };
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
      dataControllerRef.current?.abort();
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

  const openProjectConversation = useCallback((projectId: string, conversationId: string) => {
    const snapshot = stateRef.current;
    if (!snapshot.projects.some((project) => project.id === projectId)) return;
    if (snapshot.projectId === projectId) {
      selectConversationInternal(conversationId);
      return;
    }
    abortActiveDataRequests();
    bumpGeneration();
    writeStorage(projectStorageKey(snapshot.userId), projectId);
    writeStorage(conversationStorageKey(snapshot.userId), conversationId);
    dispatchSync({ type: "projectConversationSelected", projectId, conversationId });
    void loadConversationsInternal({ refreshOnly: true });
  }, [abortActiveDataRequests, bumpGeneration, dispatchSync, loadConversationsInternal, selectConversationInternal]);

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

  /** 新建按钮只切到空白聊天页，不向服务端创建记录。 */
  const startNewConversation = useCallback((targetProjectId?: string | null) => {
    const snapshot = stateRef.current;
    if (!snapshot.contextReady) return;
    const projectId = targetProjectId ?? "";
    if (projectId && !snapshot.projects.some((project) => project.id === projectId)) return;
    const currentDraftUnsent = (!snapshot.conversationId && !snapshot.activeLocalSubmissionId) || (
      Boolean(snapshot.conversationId) &&
      snapshot.conversationId === snapshot.draftConversationId &&
      !snapshot.optimisticConversations.some((item) => item.id === snapshot.conversationId)
    );
    if (snapshot.projectId === projectId && currentDraftUnsent) return;
    abortActiveDataRequests();
    bumpGeneration();
    writeStorage(projectStorageKey(snapshot.userId), projectId);
    writeStorage(conversationStorageKey(snapshot.userId), "");
    dispatchSync({ type: "newDraftStarted", projectId });
    dispatchSync({ type: "runStatus", runStatus: null });
    if (projectId && projectId !== snapshot.projectId) {
      void loadConversationsInternal({ refreshOnly: true });
    }
  }, [abortActiveDataRequests, bumpGeneration, dispatchSync, loadConversationsInternal]);

  /** 首次发送或普通会话附件上传时才创建持久会话。 */
  const createConversationInternal = useCallback(async () => {
    const snapshot = stateRef.current;
    if (!snapshot.contextReady || snapshot.status?.status !== "ready") {
      message.error("服务仍在准备中，请稍候再试。");
      return null;
    }
    const projectId = snapshot.projectId;
    const generation = bumpGeneration();
    const { userId, tenantId } = snapshot;
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
    message,
  ]);

  const ensureConversation = useCallback(() => {
    const snapshot = stateRef.current;
    if (snapshot.conversationId) {
      return Promise.resolve({ id: snapshot.conversationId, project_id: snapshot.projectId || null });
    }
    if (creationPromiseRef.current) return creationPromiseRef.current;
    const promise = createConversationInternal();
    creationPromiseRef.current = promise;
    void promise.finally(() => {
      if (creationPromiseRef.current === promise) creationPromiseRef.current = null;
    });
    return promise;
  }, [createConversationInternal]);

  const markConversationSubmitted = useCallback((conversationId: string, projectId: string, content: string, localOnly = false) => {
    const title = content.trim().replace(/\s+/g, " ").slice(0, 30) || "附件消息";
    dispatchSync({ type: "conversationSubmitted", conversation: {
      id: conversationId,
      project_id: projectId || null,
      title,
      updated_at: new Date().toISOString(),
      localOnly,
    } });
  }, [dispatchSync]);

  const identifyConversationSubmission = useCallback((localId: string, conversationId: string) => {
    dispatchSync({ type: "conversationSubmissionIdentified", localId, conversationId });
  }, [dispatchSync]);

  const cancelConversationSubmission = useCallback((conversationId: string) => {
    dispatchSync({ type: "conversationSubmissionFailed", conversationId });
  }, [dispatchSync]);

  const acknowledgeConversationRows = useCallback((conversationIds: string[]) => {
    if (conversationIds.some((id) => stateRef.current.optimisticConversations.some((item) => item.id === id))) {
      dispatchSync({ type: "conversationListed", conversationIds });
    }
  }, [dispatchSync]);

  const markConversationStarted = useCallback((conversationId: string, projectId: string) => {
    dispatchSync({ type: "conversationStarted", conversationId });
    if (stateRef.current.projectId === projectId) void loadConversationsInternal({ refreshOnly: true });
    else if (!projectId) void loadRecentsInternal();
  }, [dispatchSync, loadConversationsInternal, loadRecentsInternal]);

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
      getCurrentContext,
      changeUser,
      openProject,
      openProjectConversation,
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
      startNewConversation,
      ensureConversation,
      markConversationSubmitted,
      identifyConversationSubmission,
      cancelConversationSubmission,
      acknowledgeConversationRows,
      markConversationStarted,
      setBusy,
      setRunStatus,
      attachStream,
      markConversationRunning,
      markConversationIdle,
    }),
    [
      state,
      getCurrentContext,
      changeUser,
      openProject,
      openProjectConversation,
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
      startNewConversation,
      ensureConversation,
      markConversationSubmitted,
      identifyConversationSubmission,
      cancelConversationSubmission,
      acknowledgeConversationRows,
      markConversationStarted,
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
