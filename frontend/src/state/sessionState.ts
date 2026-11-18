import type {
  ConversationSummary,
  DevUser,
  ModelOption,
  Project,
  ServiceStatus,
  SkillOption,
} from "../types/api";

/** 聊天流写入的运行状态。当前界面组件不读取该字段。 */
export type RunStatus =
  | "starting"
  | "ready"
  | "selecting_tools"
  | "thinking"
  | "waiting_model"
  | "preparing_tools"
  | "preparing_file"
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
  projectId: string;
  conversationId: string | null;
  /** 已建库但尚未发送首条消息的会话；侧栏列表此时仍不展示它。 */
  draftConversationId: string | null;
  /** 首次发送后立即显示，直到服务端列表包含该会话。 */
  optimisticConversations: (ConversationSummary & { localOnly?: boolean })[];
  activeLocalSubmissionId: string | null;
  /** 首条消息落库时递增，通知侧栏刷新展开的非当前项目。 */
  conversationListRevision: number;
  /** projects 加载成功，用户上下文就绪。 */
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

export type SessionAction =
  | { type: "taskConversationsUpdated"; items: ConversationSummary[] }
  | { type: "status"; status: ServiceStatus }
  | {
      type: "bootstrapUsers";
      users: DevUser[];
      userId: string;
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
  | { type: "userSwitched"; userId: string; resetContext: boolean }
  | { type: "conversationCreated"; conversation: ConversationSummary }
  | { type: "conversationSubmitted"; conversation: ConversationSummary & { localOnly?: boolean } }
  | { type: "conversationSubmissionIdentified"; localId: string; conversationId: string }
  | { type: "conversationSubmissionFailed"; conversationId: string }
  | { type: "conversationListed"; conversationIds: string[] }
  | { type: "conversationStarted"; conversationId: string }
  | { type: "conversationMoved" }
  | { type: "busy"; busy: boolean }
  | { type: "creating"; creating: boolean }
  | { type: "runStatus"; runStatus: RunStatus | null }
  | { type: "runStarted"; conversationId: string }
  | { type: "runFinished"; conversationId: string }
  | { type: "runsCleared" }
  | { type: "epoch" }
  | { type: "bootstrapped" };

export const INITIAL_STATE: SessionState = {
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
  userId: "",
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

function mergeConversations(existing: ConversationSummary[], incoming: ConversationSummary[]): ConversationSummary[] {
  const byId = new Map(existing.map(item => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned))
    || (b.updated_at ?? "").localeCompare(a.updated_at ?? "") || b.id.localeCompare(a.id));
}

export function reducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "status":
      return { ...state, status: action.status };
    case "bootstrapUsers":
      return {
        ...state,
        users: action.users,
        userId: action.userId,
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
        ? mergeConversations(state.conversations, action.items)
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
    case "taskConversationsUpdated": {
      return {
        ...state,
        recents: mergeConversations(state.recents, action.items),
        conversations: state.projectId ? state.conversations : mergeConversations(state.conversations, action.items),
      };
    }
    case "recentsLoaded": {
      const recents = action.append
        ? mergeConversations(state.recents, action.items)
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
              skills: [],
              skillsLoading: false,
              skillsError: null,
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
    case "conversationMoved":
      return { ...state, conversationListRevision: state.conversationListRevision + 1 };
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
