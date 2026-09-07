const USER_STORAGE_KEY = "melonclaw.user_id.v3";
const TENANT_STORAGE_KEY = "melonclaw.tenant_id.v1";
const CONVERSATION_STORAGE_PREFIX = "melonclaw.conversation_id.";
const PROJECT_STORAGE_PREFIX = "melonclaw.project_id.";
const SIDEBAR_STORAGE_KEY = "melonclaw.sidebar_collapsed.v1";

const state = {
  userId: localStorage.getItem(USER_STORAGE_KEY) || "",
  tenantId: localStorage.getItem(TENANT_STORAGE_KEY) || "",
  projectId: "",
  conversationId: null,
  busy: false,
  conversationCreating: false,
  createConversationPromise: null,
  currentAssistant: null,
  approval: null,
  toolNodes: new Map(),
  subagentNodes: new Map(),
  generation: 0,
  streamController: null,
  dataController: null,
  conversations: [],
  conversationCursor: null,
  projects: [],
  users: [],
  serviceStatus: "starting",
  contextReady: false,
  composing: false,
  previousFocus: null,
  bootstrapped: false,
};

const $ = (selector) => document.querySelector(selector);
const isMobileLayout = () => window.matchMedia("(max-width: 768px)").matches;

function icon(name, className = "") {
  const node = document.createElement("span");
  node.className = `icon ${className}`.trim();
  node.dataset.icon = name;
  node.setAttribute("aria-hidden", "true");
  return node;
}

function setIcon(node, name) {
  if (node) node.dataset.icon = name;
}

function conversationStorageKey(userId = state.userId) {
  return `${CONVERSATION_STORAGE_PREFIX}${userId}`;
}

function projectStorageKey(userId = state.userId) {
  return `${PROJECT_STORAGE_PREFIX}${userId}`;
}

function safeDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatConversationTime(value) {
  const date = safeDate(value);
  if (!date) return "";
  return date.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatMessageTime(value) {
  const date = safeDate(value);
  if (!date) return "";
  return date.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function currentUserDisplayName() {
  const user = state.users.find((item) => item.user_id === state.userId);
  return user?.user_name_zh || user?.display_name || user?.username || state.userId || "模拟用户";
}

function userInitials(name) {
  const clean = String(name || "模拟用户").trim();
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length > 1 && /^[A-Za-z]/.test(clean)) return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  return Array.from(clean.replace(/\s/g, "")).slice(0, 2).join("") || "我";
}

function setPickerAvatar(node, name, userId = "") {
  if (!node) return;
  const palette = [
    ["#dceee0", "#176b4a"],
    ["#fbe5d8", "#9a5142"],
    ["#e5e4f5", "#4e548a"],
    ["#f3eacb", "#785f18"],
  ];
  const hash = Array.from(String(userId || name)).reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const [background, color] = palette[hash % palette.length];
  node.textContent = userInitials(name);
  node.style.backgroundColor = background;
  node.style.color = color;
}

function setMessageAvatar(node) {
  if (!node) return;
  const image = document.createElement("img");
  image.src = "/static/assets/brand/melon.png";
  image.alt = "";
  node.replaceChildren(image);
}

function setRunStatus(status, label = "") {
  const pill = $("#run-status-pill");
  const text = $("#run-status-text");
  const statusIcon = $("#run-status-icon");
  if (!pill || !text || !statusIcon) return;
  const labels = {
    starting: "连接中",
    ready: "已就绪",
    processing: "处理中",
    waiting: "等待确认",
    failed: "失败",
  };
  const icons = {
    starting: "loader-circle",
    ready: "circle-check",
    processing: "loader-circle",
    waiting: "shield-check",
    failed: "circle-alert",
  };
  pill.dataset.status = status;
  setIcon(statusIcon, icons[status] || "circle-alert");
  text.textContent = label || labels[status] || status;
}

function setStatus(status, message = "") {
  state.serviceStatus = status;
  const dot = $("#status-dot");
  const text = $("#status-text");
  if (dot) dot.className = `status-dot ${status === "starting" ? "is-loading" : status === "error" ? "is-error" : ""}`;
  if (text) text.textContent = status === "ready" ? "服务已就绪" : status === "error" ? "启动失败" : "正在启动助手";
  if (status === "error") {
    setRunStatus("failed", "失败");
    showError(message || "无法启动助手，请检查配置。");
  } else if (status === "starting") {
    setRunStatus("starting", "连接中");
  } else if (!state.busy && !state.conversationCreating) {
    setRunStatus("ready", "已就绪");
  }
}

function showError(message) {
  const old = $(".system-error");
  if (old) old.remove();
  const node = document.createElement("div");
  node.className = "system-error";
  node.setAttribute("role", "alert");
  node.textContent = message;
  $("#conversation")?.prepend(node);
}

function announce(message) {
  const region = $("#sr-status");
  if (region) region.textContent = message;
}

function clearError() {
  $(".system-error")?.remove();
}

function isNearConversationBottom() {
  const conversation = $("#conversation");
  return conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 96;
}

function scrollConversationToBottom({ smooth = false } = {}) {
  const conversation = $("#conversation");
  conversation.scrollTo({ top: conversation.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      if (body.error || body.message) message = body.error || body.message;
    } catch (_) { /* keep status message */ }
    throw new Error(message);
  }
  return response;
}

async function refreshStatus() {
  try {
    const response = await api("/api/status");
    const info = await response.json();
    setStatus(info.status, info.message);
    if (info.model) $("#model-text").textContent = `${info.provider || "model"}:${info.model}`;
    $("#mcp-text").textContent = info.mcp_servers?.length
      ? `MCP · ${info.mcp_servers.join(" · ")}`
      : info.database === "connected" ? "MCP · 未配置" : "数据库连接中";
    if (info.status === "ready" && !state.bootstrapped) {
      await loadUsers();
      await loadProjects();
      await loadConversations();
      state.bootstrapped = true;
    }
    if (info.status === "starting") window.setTimeout(refreshStatus, 1200);
  } catch (error) {
    setStatus("error", error.message);
  }
}

function setUserPickerOpen(open) {
  const trigger = $("#user-select");
  const options = $("#user-options");
  if (!trigger || !options) return;
  trigger.setAttribute("aria-expanded", String(open));
  options.hidden = !open;
}

function renderUserPicker() {
  const triggerValue = $("#user-select-value");
  const triggerAvatar = $("#user-avatar");
  const options = $("#user-options");
  if (!triggerValue || !options) return;
  options.replaceChildren();
  state.users.forEach((user) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `user-option${user.user_id === state.userId ? " is-selected" : ""}`;
    option.dataset.userId = user.user_id;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(user.user_id === state.userId));
    const displayName = user.display_name || user.user_name_zh || user.username || user.user_id;
    const avatar = document.createElement("span");
    avatar.className = "user-avatar option-avatar";
    avatar.setAttribute("aria-hidden", "true");
    setPickerAvatar(avatar, displayName, user.user_id);
    const label = document.createElement("span");
    label.className = "user-option-label";
    label.textContent = displayName;
    option.append(avatar, label);
    option.addEventListener("click", () => {
      setUserPickerOpen(false);
      handleUserChange(user.user_id);
    });
    options.append(option);
  });
  const selected = state.users.find((user) => user.user_id === state.userId);
  const displayName = selected?.user_name_zh || selected?.display_name || selected?.username || "请选择用户";
  triggerValue.textContent = displayName;
  setPickerAvatar(triggerAvatar, displayName, state.userId);
}

async function loadUsers() {
  const response = await api("/api/dev/users");
  const data = await response.json();
  state.users = [...(data.items || [])];
  let selected = state.users.find((item) => item.user_id === state.userId);
  if (!selected) {
    selected = state.users.find((item) => item.is_default) || state.users[0] || null;
    state.userId = selected?.user_id || "";
  }
  const tenantIds = selected?.tenant_ids || (selected?.tenant_id ? [selected.tenant_id] : []);
  if (!tenantIds.includes(state.tenantId)) state.tenantId = selected?.default_tenant_id || tenantIds[0] || "";
  renderUserPicker();
  localStorage.setItem(USER_STORAGE_KEY, state.userId);
  localStorage.setItem(TENANT_STORAGE_KEY, state.tenantId);
}

function projectById(projectId) {
  return state.projects.find((project) => project.id === projectId) || null;
}

function updateProjectContext() {
  const project = projectById(state.projectId);
  const name = project?.name || "未选择项目";
  const currentProject = $("#current-project-name");
  const topbarProject = $("#topbar-project");
  if (currentProject) currentProject.textContent = name;
  if (topbarProject) topbarProject.textContent = name;
}

async function loadProjects() {
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const response = await api(`/api/projects?${new URLSearchParams({ user_id: userId, tenant_id: tenantId })}`);
  const data = await response.json();
  if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId) return;
  state.projects = [...(data.items || [])];
  const saved = localStorage.getItem(projectStorageKey());
  state.projectId = saved && state.projects.some((project) => project.id === saved) ? saved : "";
  state.contextReady = true;
  localStorage.setItem(projectStorageKey(), state.projectId);
  renderProjectList();
  renderConversationPanel();
  updateComposer();
}

function renderProjectList() {
  const list = $("#project-list");
  list.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("div");
    empty.className = "project-empty";
    empty.textContent = "还没有项目，点击“新增项目”开始。";
    list.append(empty);
    return;
  }
  state.projects.forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.id === state.projectId ? " is-open" : ""}`;
    button.dataset.projectId = project.id;
    button.title = project.name;
    const projectIcon = icon("folder");
    const copy = document.createElement("span");
    copy.className = "project-item-copy";
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = project.name;
    const meta = document.createElement("span");
    meta.className = "project-meta";
    meta.textContent = project.is_default ? "默认工作区" : "项目工作区";
    copy.append(name, meta);
    const arrow = icon("chevron-right", "project-arrow");
    button.append(projectIcon, copy, arrow);
    button.addEventListener("click", () => openProject(project.id));
    list.append(button);
  });
}

function renderConversationPanel() {
  const panel = $("#conversation-section");
  const project = projectById(state.projectId);
  if (!panel) return;
  panel.hidden = !project;
  updateProjectContext();
  if (!project) {
    $("#conversation-list").replaceChildren();
    $("#load-more").hidden = true;
  }
}

function renderConversationList() {
  const list = $("#conversation-list");
  list.replaceChildren();
  if (!state.conversations.length) {
    const empty = document.createElement("div");
    empty.className = "conversation-empty";
    empty.textContent = "该项目还没有会话，点击“新建对话”开始。";
    list.append(empty);
  } else {
    state.conversations.forEach((conversation) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `conversation-item${conversation.id === state.conversationId ? " is-active" : ""}`;
      button.dataset.conversationId = conversation.id;
      button.title = conversation.title || "新会话";
      const title = document.createElement("div");
      title.className = "conversation-title";
      title.textContent = conversation.title || "新会话";
      const time = document.createElement("div");
      time.className = "conversation-time";
      time.textContent = formatConversationTime(conversation.updated_at);
      button.append(title, time);
      button.addEventListener("click", () => selectConversation(conversation.id));
      list.append(button);
    });
  }
  const more = $("#load-more");
  more.hidden = !state.conversationCursor;
  more.disabled = false;
}

function resetConversationRenderState() {
  state.currentAssistant = null;
  state.approval = null;
  state.toolNodes.clear();
  state.subagentNodes.clear();
  $("#approval-slot").replaceChildren();
}

function buildWelcome() {
  const welcome = document.createElement("div");
  welcome.className = "welcome";
  const mark = document.createElement("div");
  mark.className = "welcome-mark";
  const image = document.createElement("img");
  image.src = "/static/assets/brand/melonclaw-mark.png";
  image.alt = "";
  mark.append(image);
  const kicker = document.createElement("div");
  kicker.className = "welcome-kicker";
  kicker.textContent = "你好，我是 MelonClaw";
  const title = document.createElement("h1");
  title.textContent = "今天，有什么想一起搞定的？";
  const copy = document.createElement("p");
  copy.className = "welcome-copy";
  copy.textContent = "查资料、理思路、做计划，瓜爪来帮你。";
  const prompts = [
    ["search", "查找资料", "比较方案，梳理可靠信息", "请比较两个技术方案的优缺点，并给出适用场景和推荐结论。"],
    ["list-checks", "整理思路", "把杂乱内容变成行动清单", "请把下面这段内容整理成清晰的要点清单，并标出待确认的问题。"],
    ["calendar-days", "制定计划", "拆解目标，安排节奏与下一步", "请根据我的目标制定一周学习计划，安排每天的重点、时间和复盘方式。"],
  ];
  const grid = document.createElement("div");
  grid.className = "prompt-grid";
  prompts.forEach(([iconName, label, description, prompt]) => {
    const button = document.createElement("button");
    button.className = "prompt-card";
    button.type = "button";
    button.dataset.prompt = prompt;
    const promptIcon = icon(iconName, "prompt-icon");
    const text = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = label;
    const small = document.createElement("small");
    small.textContent = description;
    text.append(strong, small);
    button.append(promptIcon, text, icon("chevron-right", "prompt-arrow"));
    grid.append(button);
  });
  welcome.append(mark, kicker, title, copy, grid);
  return welcome;
}

function clearConversationView(title = "准备开始", copy = "选择左侧项目，或直接输入第一条消息。", options = {}) {
  resetConversationRenderState();
  const conversation = $("#conversation");
  conversation.replaceChildren();
  $("#session-label").textContent = title;
  if (options.welcome) {
    conversation.append(buildWelcome());
    return;
  }
  const empty = document.createElement("div");
  empty.className = "empty-conversation";
  const mark = document.createElement("img");
  mark.className = "empty-mark";
  mark.src = "/static/assets/brand/melonclaw-mark.png";
  mark.alt = "";
  const heading = document.createElement("div");
  heading.className = "empty-conversation-title";
  heading.textContent = title;
  const description = document.createElement("div");
  description.className = "empty-conversation-copy";
  description.textContent = copy;
  empty.append(mark, heading, description);
  if (options.newProject) {
    const action = document.createElement("button");
    action.className = "empty-action";
    action.type = "button";
    action.textContent = "新增项目";
    action.addEventListener("click", openProjectDialog);
    empty.append(action);
  }
  conversation.append(empty);
}

function renderContextEmpty() {
  if (!state.projects.length) {
    clearConversationView("还没有可用项目", "先创建一个项目，瓜爪会为它保存独立的会话和工作目录。", { newProject: true });
  } else {
    clearConversationView("准备开始", "选择一个项目，或直接输入第一条消息。", { welcome: true });
  }
  updateComposer();
}

function closeProject() {
  abortActiveRequests();
  state.generation += 1;
  state.projectId = "";
  localStorage.setItem(projectStorageKey(), "");
  state.conversationId = null;
  state.conversations = [];
  state.conversationCursor = null;
  renderProjectList();
  renderConversationPanel();
  renderContextEmpty();
}

async function openProject(projectId) {
  const project = projectById(projectId);
  if (!project) return;
  if (state.projectId === project.id) {
    closeProject();
    return;
  }
  abortActiveRequests();
  state.generation += 1;
  state.projectId = project.id;
  localStorage.setItem(projectStorageKey(), state.projectId);
  state.conversationId = null;
  state.conversations = [];
  state.conversationCursor = null;
  renderProjectList();
  renderConversationPanel();
  clearConversationView(project.name, "正在加载这个项目里的会话…");
  updateComposer();
  await loadConversations();
}

async function loadConversations({ append = false, refreshOnly = false } = {}) {
  if (!state.projectId) {
    state.conversations = [];
    state.conversationCursor = null;
    renderConversationPanel();
    if (!append && !refreshOnly) renderConversationList();
    if (!append && !refreshOnly) renderContextEmpty();
    return;
  }
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const projectId = state.projectId;
  const cursor = append ? state.conversationCursor : null;
  const query = new URLSearchParams({ user_id: userId, tenant_id: tenantId, limit: "20", project_id: projectId });
  if (cursor) query.set("cursor", cursor);
  const controller = new AbortController();
  state.dataController = controller;
  try {
    const response = await api(`/api/conversations?${query.toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId || projectId !== state.projectId) return;
    state.conversations = append ? [...state.conversations, ...(data.items || [])] : (data.items || []);
    state.conversationCursor = data.next_cursor || null;
    renderConversationList();
    if (refreshOnly || append) return;
    const saved = localStorage.getItem(conversationStorageKey());
    const selected = state.conversations.find((item) => item.id === saved) || state.conversations[0];
    if (selected) {
      await selectConversation(selected.id);
    } else {
      state.conversationId = null;
      clearConversationView("准备开始", "选择一个项目，或直接输入第一条消息。", { welcome: true });
      updateComposer();
    }
  } catch (error) {
    if (error.name !== "AbortError") showError(error.message);
  } finally {
    if (state.dataController === controller) state.dataController = null;
  }
}

function abortActiveStream() {
  if (state.streamController) state.streamController.abort();
  state.streamController = null;
  state.busy = false;
  state.currentAssistant = null;
  state.approval = null;
  $("#approval-slot").replaceChildren();
  updateComposer();
}

function abortDataRequest() {
  if (state.dataController) state.dataController.abort();
  state.dataController = null;
}

function abortActiveRequests() {
  abortActiveStream();
  abortDataRequest();
}

function chooseDefaultProject() {
  return projectById(state.projectId) || state.projects.find((project) => project.is_default) || state.projects[0] || null;
}

async function createConversation({ fromSend = false } = {}) {
  if (state.busy || state.conversationCreating) return null;
  if (!state.contextReady || state.serviceStatus !== "ready") {
    showError("服务仍在准备中，请稍候再试。");
    return null;
  }
  const project = chooseDefaultProject();
  if (!project) {
    showError("还没有可用项目，请先创建一个项目。");
    if (fromSend) openProjectDialog();
    return null;
  }
  if (!state.projectId) {
    state.projectId = project.id;
    localStorage.setItem(projectStorageKey(), project.id);
    renderProjectList();
    renderConversationPanel();
  }
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const projectId = project.id;
  state.conversationCreating = true;
  setRunStatus("processing", "准备会话");
  updateComposer();
  try {
    const response = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, tenant_id: tenantId, project_id: projectId }),
    });
    const conversation = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId || projectId !== state.projectId) return null;
    state.conversationId = conversation.id;
    localStorage.setItem(conversationStorageKey(), state.conversationId);
    state.generation += 1;
    clearConversationView("新会话", "输入第一条消息，开始与助手聊天。");
    await loadConversations({ refreshOnly: true });
    renderConversationList();
    announce("已创建新会话");
    return conversation;
  } catch (error) {
    showError(error.message);
    announce("会话创建失败，输入内容仍保留");
    return null;
  } finally {
    state.conversationCreating = false;
    if (!state.busy && state.serviceStatus === "ready") setRunStatus("ready", "已就绪");
    updateComposer();
  }
}

function ensureConversation() {
  if (state.conversationId) return Promise.resolve(state.conversationId);
  if (state.createConversationPromise) return state.createConversationPromise;
  state.createConversationPromise = createConversation({ fromSend: true }).finally(() => {
    state.createConversationPromise = null;
  });
  return state.createConversationPromise.then((conversation) => conversation?.id || null);
}

function openProjectDialog() {
  const dialog = $("#project-dialog");
  const input = $("#project-name-input");
  const error = $("#project-dialog-error");
  if (!dialog || !input) return;
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
  input.value = "";
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
    window.setTimeout(() => input.focus(), 0);
  }
}

function closeProjectDialog() {
  const dialog = $("#project-dialog");
  if (dialog?.open) dialog.close();
}

async function createProject(name) {
  if (state.busy || state.conversationCreating) return false;
  const cleanName = String(name || "").trim();
  const dialogError = $("#project-dialog-error");
  if (!cleanName) {
    if (dialogError) {
      dialogError.hidden = false;
      dialogError.textContent = "请输入项目名称。";
    }
    $("#project-name-input")?.focus();
    return false;
  }
  clearError();
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const submit = $("#project-dialog-submit");
  if (submit) submit.disabled = true;
  try {
    const response = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, tenant_id: tenantId, name: cleanName }),
    });
    const project = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId) return false;
    state.projectId = project.id;
    localStorage.setItem(projectStorageKey(), project.id);
    state.conversationId = null;
    state.conversations = [];
    state.conversationCursor = null;
    closeProjectDialog();
    await loadProjects();
    renderProjectList();
    renderConversationPanel();
    await loadConversations();
    announce(`已创建项目：${project.name}`);
    return true;
  } catch (error) {
    if (dialogError) {
      dialogError.hidden = false;
      dialogError.textContent = error.message;
    }
    showError(error.message);
    return false;
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function selectConversation(conversationId) {
  if (!conversationId) return;
  abortActiveRequests();
  state.generation += 1;
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const projectId = state.projectId;
  state.conversationId = conversationId;
  localStorage.setItem(conversationStorageKey(), conversationId);
  renderConversationList();
  clearConversationView("正在加载会话", "历史消息加载中…");
  updateComposer();
  closeSidebar();
  const controller = new AbortController();
  state.dataController = controller;
  try {
    const query = new URLSearchParams({ user_id: userId, tenant_id: tenantId, limit: "50" });
    const response = await api(`/api/conversations/${conversationId}/messages?${query.toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId || projectId !== state.projectId || conversationId !== state.conversationId) return;
    renderHistory(data);
  } catch (error) {
    if (error.name !== "AbortError" && generation === state.generation) showError(error.message);
  } finally {
    if (state.dataController === controller) state.dataController = null;
  }
}

function statusLabel(status) {
  return {
    pending: "处理中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "等待确认",
  }[status] || "";
}

function addMessage(kind, { messageId = null, status = null, timestamp = null, scroll = true } = {}) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  if (messageId) article.dataset.messageId = messageId;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  if (kind === "user") {
    avatar.classList.add("user-avatar");
    setMessageAvatar(avatar);
  } else {
    avatar.classList.add("assistant-avatar");
    const image = document.createElement("img");
    image.src = "/static/assets/brand/melonclaw-mark.png";
    image.alt = "MelonClaw";
    avatar.append(image);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("span");
  name.className = "message-author";
  name.textContent = kind === "user" ? currentUserDisplayName() : "MelonClaw";
  meta.append(name);
  const time = document.createElement("span");
  time.className = "message-time";
  const timeText = kind === "user" ? formatMessageTime(timestamp) : statusLabel(status) || "";
  if (kind === "assistant" || timeText) {
    time.textContent = timeText;
    time.hidden = !timeText;
    meta.append(time);
  }
  const body = document.createElement("div");
  body.className = "message-body";
  body.dataset.rawContent = "";
  const tools = document.createElement("div");
  tools.className = "message-tools";
  content.append(meta, body, tools);
  if (kind === "assistant") {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copy = document.createElement("button");
    copy.className = "copy-button";
    copy.type = "button";
    copy.setAttribute("aria-label", "复制助手回复");
    copy.append(icon("copy"), document.createTextNode("复制"));
    const feedback = document.createElement("span");
    feedback.className = "copy-feedback";
    feedback.setAttribute("aria-live", "polite");
    actions.append(copy, feedback);
    content.append(actions);
  }
  article.append(avatar, content);
  $("#conversation").append(article);
  $(".empty-conversation")?.remove();
  if (scroll) article.scrollIntoView({ behavior: "smooth", block: "end" });
  return { article, body, tools };
}

function messageById(messageId) {
  const article = [...$("#conversation").querySelectorAll("article.message")].find((node) => node.dataset.messageId === messageId);
  if (!article) return null;
  return { article, body: article.querySelector(".message-body"), tools: article.querySelector(".message-tools") };
}

function ensureAssistant(messageId = null) {
  if (!state.currentAssistant) state.currentAssistant = messageById(messageId) || addMessage("assistant", { messageId });
  if (messageId && !state.currentAssistant.article.dataset.messageId) state.currentAssistant.article.dataset.messageId = messageId;
  return state.currentAssistant;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

function safeHref(value) {
  const href = String(value || "").trim();
  if (/^(https?:|mailto:)/i.test(href) || href.startsWith("#") || (href.startsWith("/") && !href.startsWith("//"))) return href;
  return "";
}

function renderInline(markdown) {
  const tokens = [];
  const stash = (html) => {
    const marker = `\u0000${tokens.length}\u0000`;
    tokens.push(html);
    return marker;
  };
  let source = String(markdown || "");
  source = source.replace(/!\[([^\]]*)\]\([^)]*\)/g, (_, alt) => stash(`<span class="blocked-image">[图片：${escapeHtml(alt || "未命名")}]</span>`));
  source = source.replace(/`([^`\n]+)`/g, (_, code) => stash(`<code>${escapeHtml(code)}</code>`));
  source = source.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^)]*["'])?\)/g, (_, label, url) => {
    const href = safeHref(url);
    if (!href) return label;
    return stash(`<a href="${escapeAttribute(href)}" target="_blank" rel="noreferrer noopener">${renderInline(label)}</a>`);
  });
  let html = escapeHtml(source);
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
  return html.replace(/\u0000(\d+)\u0000/g, (_, index) => tokens[Number(index)] || "");
}

function parseTableRow(line) {
  let value = String(line).trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line) {
  const cells = parseTableRow(line);
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function buildCodeBlock(code, language = "") {
  const wrapper = `<div class="code-block"><div class="code-block-head"><span>${escapeHtml(language || "code")}</span><button type="button" class="code-copy" data-code-copy="true"><span class="icon" data-icon="copy" aria-hidden="true"></span><span>复制代码</span></button></div><pre><code>${escapeHtml(code)}</code></pre></div>`;
  return wrapper;
}

function renderMarkdown(source) {
  const lines = String(source || "").replaceAll("\r\n", "\n").split("\n");
  const blocks = [];
  let index = 0;
  const blockStart = (line) => /^(\s*```|\s{0,3}#{1,3}\s|\s*[-*+]\s+|\s*\d+[.)]\s+|\s*>\s?)/.test(line);
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^\s*```\s*([\w-]*)\s*$/);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(buildCodeBlock(code.join("\n"), fence[1]));
      continue;
    }
    const heading = line.match(/^\s*(#{1,3})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const headers = parseTableRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) rows.push(parseTableRow(lines[index++]));
      const headHtml = headers.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
      const rowHtml = rows.map((row) => `<tr>${headers.map((_, cellIndex) => `<td>${renderInline(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("");
      blocks.push(`<div class="markdown-table"><table><thead><tr>${headHtml}</tr></thead><tbody>${rowHtml}</tbody></table></div>`);
      continue;
    }
    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    if (unordered) {
      const items = [];
      while (index < lines.length) {
        const match = lines[index].match(/^\s*[-*+]\s+(.+)$/);
        if (!match) break;
        items.push(`<li>${renderInline(match[1])}</li>`);
        index += 1;
      }
      blocks.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) {
      const items = [];
      while (index < lines.length) {
        const match = lines[index].match(/^\s*\d+[.)]\s+(.+)$/);
        if (!match) break;
        items.push(`<li>${renderInline(match[1])}</li>`);
        index += 1;
      }
      blocks.push(`<ol>${items.join("")}</ol>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) quote.push(renderInline(lines[index++].replace(/^\s*>\s?/, "")));
      blocks.push(`<blockquote>${quote.join("<br>")}</blockquote>`);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !blockStart(lines[index]) && !(lines[index].includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1]))) paragraph.push(lines[index++]);
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  }
  return blocks.join("");
}

function hydrateCodeCopyButtons(body) {
  body.querySelectorAll(".code-block").forEach((block) => {
    const button = block.querySelector("[data-code-copy]");
    const code = block.querySelector("code");
    if (button && code) button.dataset.copyText = code.textContent || "";
  });
}

function setMessageContent(message, content, { markdown = false } = {}) {
  if (!message?.body) return;
  const raw = String(content || "");
  message.body.dataset.rawContent = raw;
  if (markdown) {
    message.body.innerHTML = raw ? renderMarkdown(raw) : "";
    hydrateCodeCopyButtons(message.body);
  } else {
    message.body.textContent = raw;
  }
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器拒绝了复制操作");
}

function showCopyFeedback(button, feedback, ok) {
  if (button) button.lastChild.textContent = ok ? "已复制" : "复制失败";
  if (feedback) {
    feedback.textContent = ok ? "已复制到剪贴板" : "复制失败，请手动选择文本";
    feedback.classList.toggle("is-error", !ok);
  }
  window.setTimeout(() => {
    if (button) button.lastChild.textContent = button.dataset.codeCopy ? "复制代码" : "复制";
    if (feedback) feedback.textContent = "";
  }, 1800);
}

async function handleCopyClick(button) {
  const article = button.closest("article");
  const body = article?.querySelector(".message-body");
  const feedback = article?.querySelector(".copy-feedback");
  const value = button.dataset.copyText ?? body?.dataset.rawContent ?? "";
  try {
    await copyText(value);
    showCopyFeedback(button, feedback, true);
  } catch (_) {
    showCopyFeedback(button, feedback, false);
  }
}

const TOOL_LABELS = {
  search: "搜索资料",
  tavily_search: "搜索资料",
  read_file: "读取文件",
  write_file: "写入文件",
  edit_file: "编辑文件",
  delete_file: "删除文件",
  glob: "查找文件",
  grep: "查找内容",
  ls: "查看目录",
  execute: "执行计算",
  eval: "执行计算",
  task: "委派子 Agent",
  search_memory: "检索记忆",
  read_memory: "读取记忆",
  remember_user_memory: "保存个人记忆",
  forget_user_memory: "删除个人记忆",
  propose_tenant_memory: "提交租户记忆提案",
};

function toolSummary(name) {
  return TOOL_LABELS[name] || name || "未知工具";
}

function formatToolValue(value, fallback = "{}") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (_) {
    return String(value);
  }
}

function setToolStatus(card, status) {
  if (!card) return;
  const normalized = status === "failed" ? "failed" : status === "completed" ? "completed" : "started";
  card.classList.toggle("is-complete", normalized === "completed");
  card.classList.toggle("is-failed", normalized === "failed");
  card.dataset.status = normalized;
  const label = card.querySelector(".tool-status-label");
  const statusIcon = card.querySelector(".tool-status .icon");
  const labels = { started: "处理中", completed: "已完成", failed: "失败" };
  if (label) label.textContent = labels[normalized];
  setIcon(statusIcon, normalized === "completed" ? "circle-check" : normalized === "failed" ? "circle-alert" : "loader-circle");
}

function renderToolCall(event) {
  const assistant = ensureAssistant();
  const key = event.call_key || `anonymous:${state.toolNodes.size}:${event.name || "tool"}`;
  let card = state.toolNodes.get(key);
  if (!card) {
    card = document.createElement("details");
    card.className = "tool-card";
    card.open = event.status !== "completed";
    card.dataset.callKey = key;
    const head = document.createElement("summary");
    head.className = "tool-head";
    head.append(icon(event.name === "task" ? "list-checks" : "loader-circle"));
    const summary = document.createElement("span");
    summary.className = "tool-summary";
    summary.textContent = toolSummary(event.name);
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = event.name || "unknown";
    const status = document.createElement("span");
    status.className = "tool-status";
    status.append(icon("loader-circle"));
    const statusLabelNode = document.createElement("span");
    statusLabelNode.className = "tool-status-label";
    status.append(statusLabelNode);
    head.append(summary, name, status);
    const details = document.createElement("div");
    details.className = "tool-details";
    const args = document.createElement("pre");
    args.className = "tool-args";
    const children = document.createElement("div");
    children.className = "tool-children";
    details.append(args, children);
    card.append(head, details);
    assistant.tools.append(card);
    state.toolNodes.set(key, card);
  }
  if (event.name) {
    card.querySelector(".tool-summary").textContent = toolSummary(event.name);
    card.querySelector(".tool-name").textContent = event.name;
    setIcon(card.querySelector(".tool-head > .icon"), event.name === "task" ? "list-checks" : "loader-circle");
  }
  if (event.args !== undefined) card.querySelector(".tool-args").textContent = formatToolValue(event.args);
  setToolStatus(card, event.status || "started");
  return card;
}

function renderToolResult(event) {
  const assistant = ensureAssistant();
  const key = event.call_key || `anonymous:${state.toolNodes.size}:${event.name || "tool"}`;
  const card = state.toolNodes.get(key) || renderToolCall({ call_key: key, name: event.name, status: "started" });
  setToolStatus(card, event.status === "failed" ? "failed" : "completed");
  const output = card.querySelector(".tool-output") || document.createElement("pre");
  output.className = "tool-output";
  output.textContent = formatToolValue(event.content, "<无文本输出>");
  if (!output.parentElement) card.querySelector(".tool-details").append(output);
  if (event.status !== "failed") card.open = false;
}

function subagentStatusLabel(status) {
  return status === "failed" ? "失败" : status === "completed" ? "已完成" : "处理中";
}

function renderSubagentStarted(event) {
  const assistant = ensureAssistant();
  let node = state.subagentNodes.get(event.subagent_id);
  if (node) return node;
  const parentKeys = [];
  if (event.parent_call_id) {
    const rawParent = String(event.parent_call_id);
    parentKeys.push(rawParent.startsWith("history:") || rawParent.startsWith("id:") ? rawParent : `id:${rawParent}`);
    if (event.parent_subagent_id) {
      const parentToken = rawParent.includes(":id:") ? rawParent.split(":id:").pop() : rawParent.replace(/^id:/, "");
      parentKeys.unshift(`${event.parent_subagent_id}:id:${parentToken}`);
    }
  }
  const parentKey = parentKeys.find((key) => state.toolNodes.has(key)) || parentKeys[0] || null;
  let parent = parentKey ? state.toolNodes.get(parentKey) : null;
  if (!parent && parentKey) {
    parent = renderToolCall({ call_key: parentKey, name: "task", status: "started" });
  }
  const card = document.createElement("details");
  card.className = "subagent-card";
  card.open = true;
  card.dataset.subagentId = event.subagent_id || "";
  const head = document.createElement("summary");
  head.className = "subagent-head";
  head.append(icon("list-checks", "subagent-icon"));
  const name = document.createElement("span");
  name.className = "subagent-name";
  name.textContent = event.subagent_name || "general-purpose";
  const status = document.createElement("span");
  status.className = "subagent-status";
  status.textContent = "处理中 · 0 个工具";
  head.append(name, status);
  const body = document.createElement("div");
  body.className = "subagent-details";
  const output = document.createElement("div");
  output.className = "subagent-output";
  const tools = document.createElement("div");
  tools.className = "subagent-tools";
  body.append(output, tools);
  card.append(head, body);
  const container = parent?.querySelector(".tool-children") || assistant.tools;
  container.append(card);
  node = { card, body, output, tools, status, toolCount: 0 };
  state.subagentNodes.set(event.subagent_id, node);
  return node;
}

function renderSubagentText(event) {
  const node = renderSubagentStarted(event);
  node.output.textContent += event.text || "";
}

function renderSubagentToolCall(event) {
  const node = renderSubagentStarted(event);
  const previous = state.currentAssistant;
  state.currentAssistant = { article: node.card, body: node.output, tools: node.tools };
  renderToolCall(event);
  state.currentAssistant = previous;
  if (event.status === "started") node.toolCount += 1;
  node.status.textContent = `处理中 · ${node.toolCount} 个工具`;
}

function renderSubagentToolResult(event) {
  const node = renderSubagentStarted(event);
  const previous = state.currentAssistant;
  state.currentAssistant = { article: node.card, body: node.output, tools: node.tools };
  renderToolResult(event);
  state.currentAssistant = previous;
}

function renderSubagentTerminal(event) {
  const node = renderSubagentStarted(event);
  const failed = event.type === "subagent_failed" || event.status === "failed";
  node.card.classList.toggle("is-complete", !failed);
  node.card.classList.toggle("is-failed", failed);
  node.status.textContent = `${subagentStatusLabel(failed ? "failed" : "completed")} · ${node.toolCount} 个工具`;
  if (event.error && !node.output.textContent) node.output.textContent = event.error;
  const completed = [...state.subagentNodes.values()].filter((item) => item.card.classList.contains("is-complete"));
  if (completed.length > 2) node.card.open = false;
  announce(`${node.card.querySelector(".subagent-name").textContent} ${failed ? "执行失败" : "已完成"}`);
}

function renderStoredTools(assistant, events, messageId) {
  const previous = state.currentAssistant;
  state.currentAssistant = assistant;
  (events || []).forEach((event, index) => {
    const scopedId = event.subagent_id ? `history:${messageId}:${event.subagent_id}` : undefined;
    const scopedParent = event.parent_call_id ? `history:${messageId}:id:${event.parent_call_id}` : undefined;
    const scopedParentSubagent = event.parent_subagent_id ? `history:${messageId}:${event.parent_subagent_id}` : undefined;
    const scoped = {
      ...event,
      call_key: event.call_key ? `history:${messageId}:${event.call_key}` : `history:${messageId}:${index}`,
      ...(scopedId ? { subagent_id: scopedId } : {}),
      ...(scopedParent ? { parent_call_id: scopedParent } : {}),
      ...(scopedParentSubagent ? { parent_subagent_id: scopedParentSubagent } : {}),
    };
    if (event.type.startsWith("subagent_")) {
      if (event.type === "subagent_started") renderSubagentStarted(scoped);
      if (event.type === "subagent_text") renderSubagentText(scoped);
      if (event.type === "subagent_tool_call") renderSubagentToolCall(scoped);
      if (event.type === "subagent_tool_result") renderSubagentToolResult(scoped);
      if (event.type === "subagent_completed" || event.type === "subagent_failed") renderSubagentTerminal(scoped);
      return;
    }
    if (event.type === "tool_call") renderToolCall(scoped);
    if (event.type === "tool_result") renderToolResult(scoped);
  });
  state.currentAssistant = previous;
}

function renderHistory(data) {
  $("#conversation").replaceChildren();
  resetConversationRenderState();
  const conversation = data.conversation;
  $("#session-label").textContent = conversation.title || "聊天会话";
  updateProjectContext();
  (data.items || []).forEach((item) => {
    const message = addMessage(item.role === "user" ? "user" : "assistant", {
      messageId: item.id,
      status: item.status,
      timestamp: item.created_at,
      scroll: false,
    });
    setMessageContent(message, item.content || "", { markdown: item.role !== "user" });
    if (item.role === "assistant") {
      renderStoredTools(message, item.display_metadata?.events || [], item.id);
      if (item.status !== "completed") setMessageStatus(message, item.status);
    }
  });
  if (!data.items?.length) clearConversationView(conversation.title || "新会话", "输入第一条消息，开始与助手聊天。");
  if (data.pending_approval) {
    state.busy = true;
    renderApproval(data.pending_approval);
  } else {
    state.busy = false;
    if (state.serviceStatus === "ready") setRunStatus("ready", "已就绪");
  }
  updateComposer();
}

function setMessageStatus(message, status) {
  if (!message) return;
  const label = message.article.querySelector(".message-time");
  if (!label) return;
  label.textContent = statusLabel(status) || "";
  label.hidden = !label.textContent;
}

function restoreDraft(draft) {
  const input = $("#message-input");
  if (!input || input.value.trim() || !draft) return;
  input.value = draft;
  autoResize();
  updateComposer();
}

function renderApproval(request) {
  state.approval = request;
  const slot = $("#approval-slot");
  slot.replaceChildren();
  const panel = document.createElement("div");
  panel.className = "approval-panel";
  const title = document.createElement("div");
  title.className = "approval-title";
  title.append(icon("shield-check"), document.createTextNode("需要你确认一项操作"));
  const copy = document.createElement("div");
  copy.className = "approval-copy";
  copy.textContent = "助手提出了需要确认的操作。你可以批准、编辑参数或拒绝；编辑时不能替换工具名称。";
  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const interrupts = Array.isArray(request.interrupts) && request.interrupts.length
    ? request.interrupts
    : [{ id: request.id || "", actions: request.actions || [] }];
  let actionNumber = 0;
  interrupts.forEach((interrupt) => (interrupt.actions || []).forEach((action) => {
    actionNumber += 1;
    const row = document.createElement("div");
    row.className = "approval-action";
    row.dataset.interruptId = interrupt.id || "";
    row.dataset.toolName = action.name || "unknown";
    const top = document.createElement("div");
    top.className = "approval-action-top";
    const tool = document.createElement("div");
    tool.className = "approval-tool";
    tool.textContent = `${actionNumber}. ${toolSummary(action.name)} · ${action.name || "unknown"}`;
    const select = document.createElement("select");
    select.className = "approval-select";
    const labels = { approve: "批准", edit: "编辑参数", reject: "拒绝", respond: "返回结果" };
    (action.allowed_decisions || ["approve", "edit", "reject"]).forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = labels[choice] || choice;
      select.append(option);
    });
    top.append(tool, select);
    const description = document.createElement("div");
    description.className = "approval-description";
    description.textContent = action.description || "该操作需要你的确认后才会执行。";
    const argsText = formatToolValue(action.args);
    const argsDetails = document.createElement("details");
    const argsSummary = document.createElement("summary");
    argsSummary.className = "approval-args-summary";
    argsSummary.textContent = "查看完整参数";
    const args = document.createElement("pre");
    args.className = "approval-args";
    args.textContent = argsText;
    argsDetails.append(argsSummary, args);
    const edit = document.createElement("textarea");
    edit.className = "approval-edit";
    edit.value = argsText;
    edit.hidden = true;
    edit.setAttribute("aria-label", `${action.name || "工具"} 的编辑参数`);
    const reject = document.createElement("textarea");
    reject.className = "approval-reject";
    reject.placeholder = "拒绝原因（可选）";
    reject.hidden = true;
    reject.setAttribute("aria-label", `${action.name || "工具"} 的拒绝原因`);
    const fieldError = document.createElement("div");
    fieldError.className = "approval-field-error";
    fieldError.hidden = true;
    fieldError.setAttribute("role", "alert");
    select.addEventListener("change", () => {
      edit.hidden = select.value !== "edit";
      reject.hidden = !["reject", "respond"].includes(select.value);
      fieldError.hidden = true;
      fieldError.textContent = "";
    });
    row.append(top, description, argsDetails, edit, reject, fieldError);
    actions.append(row);
  }));
  if (!actionNumber) {
    const empty = document.createElement("div");
    empty.className = "approval-copy";
    empty.textContent = "没有可展示的操作，请刷新会话后重试。";
    actions.append(empty);
  }
  const submit = document.createElement("button");
  submit.className = "approval-submit";
  submit.type = "button";
  submit.textContent = "提交决定并继续";
  submit.addEventListener("click", () => submitApproval(panel, request));
  panel.append(title, copy, actions, submit);
  slot.append(panel);
  state.busy = true;
  setRunStatus("waiting", "等待确认");
  updateComposer();
  slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function submitApproval(panel, request) {
  const rows = [...panel.querySelectorAll(".approval-action")];
  try {
    const interrupts = Array.isArray(request.interrupts) && request.interrupts.length
      ? request.interrupts
      : [{ id: request.id || "", actions: request.actions || [] }];
    const decisionsByInterrupt = new Map(interrupts.map((interrupt) => [interrupt.id || "", []]));
    rows.forEach((row, index) => {
      const choice = row.querySelector("select").value;
      const fieldError = row.querySelector(".approval-field-error");
      fieldError.hidden = true;
      fieldError.textContent = "";
      let decision;
      if (choice === "approve") decision = { type: "approve" };
      else if (choice === "reject") decision = { type: "reject", message: row.querySelector(".approval-reject").value };
      else if (choice === "respond") decision = { type: "respond", message: row.querySelector(".approval-reject").value };
      else {
        let args;
        try {
          args = JSON.parse(row.querySelector(".approval-edit").value);
        } catch (error) {
          const validation = new Error(`第 ${index + 1} 项参数不是合法 JSON：${error.message}`);
          validation.field = fieldError;
          validation.input = row.querySelector(".approval-edit");
          throw validation;
        }
        if (!args || typeof args !== "object" || Array.isArray(args)) {
          const validation = new Error(`第 ${index + 1} 项参数必须是 JSON 对象。`);
          validation.field = fieldError;
          validation.input = row.querySelector(".approval-edit");
          throw validation;
        }
        decision = { type: "edit", edited_action: { name: row.dataset.toolName, args } };
      }
      const interruptId = row.dataset.interruptId || "";
      if (!decisionsByInterrupt.has(interruptId)) throw new Error("审批请求已变化，请刷新会话后重试。");
      decisionsByInterrupt.get(interruptId).push(decision);
    });
    const decisions = interrupts.length === 1
      ? decisionsByInterrupt.get(interrupts[0].id || "")
      : interrupts.map((interrupt) => ({ interrupt_id: interrupt.id || "", decisions: decisionsByInterrupt.get(interrupt.id || "") || [] }));
    panel.querySelector(".approval-submit").disabled = true;
    state.currentAssistant = state.currentAssistant || [...$("#conversation").querySelectorAll("article.assistant")]
      .map((article) => ({ article, body: article.querySelector(".message-body"), tools: article.querySelector(".message-tools") }))
      .pop();
    await consumeStream(`/api/conversations/${state.conversationId}/approval`, {
      user_id: state.userId,
      tenant_id: state.tenantId,
      decisions,
    }, { generation: state.generation, conversationId: state.conversationId, userId: state.userId, tenantId: state.tenantId, projectId: state.projectId });
  } catch (error) {
    panel.querySelector(".approval-submit").disabled = false;
    if (error.field) {
      error.field.hidden = false;
      error.field.textContent = error.message;
      error.input?.focus();
    } else if (error.name !== "AbortError") showError(error.message);
    state.busy = false;
    if (state.serviceStatus === "ready") setRunStatus("ready", "已就绪");
    updateComposer();
  }
}

function contextMatches({ generation, conversationId, userId, tenantId, projectId }) {
  return generation === state.generation
    && conversationId === state.conversationId
    && userId === state.userId
    && (tenantId === undefined || tenantId === state.tenantId)
    && (projectId === undefined || projectId === state.projectId);
}

function handleEvent(event, context) {
  if (!contextMatches(context)) return;
  const followConversation = isNearConversationBottom();
  if (event.type === "message_started") {
    state.currentAssistant = messageById(event.message_id) || state.currentAssistant || addMessage("assistant", { messageId: event.message_id });
    state.currentAssistant.article.dataset.messageId = event.message_id;
    if (event.user_message_id) {
      const userMessage = [...$("#conversation").querySelectorAll("article.user")].at(-1);
      if (userMessage && !userMessage.dataset.messageId) userMessage.dataset.messageId = event.user_message_id;
    }
  } else if (event.type === "text") {
    const assistant = ensureAssistant();
    setMessageContent(assistant, `${assistant.body.dataset.rawContent || ""}${event.text || ""}`);
  } else if (event.type === "tool_call") {
    renderToolCall(event);
  } else if (event.type === "tool_result") {
    renderToolResult(event);
  } else if (event.type === "subagent_started") {
    renderSubagentStarted(event);
    announce(`${event.subagent_name || "general-purpose"} 子代理开始运行`);
  } else if (event.type === "subagent_text") {
    renderSubagentText(event);
  } else if (event.type === "subagent_tool_call") {
    renderSubagentToolCall(event);
  } else if (event.type === "subagent_tool_result") {
    renderSubagentToolResult(event);
  } else if (event.type === "subagent_completed" || event.type === "subagent_failed") {
    renderSubagentTerminal(event);
  } else if (event.type === "approval_required") {
    renderApproval(event.request || { actions: [] });
    announce("助手正在等待人工审批");
  } else if (event.type === "completed") {
    const assistant = messageById(event.message_id) || ensureAssistant(event.message_id);
    assistant.article.dataset.messageId = event.message_id;
    setMessageContent(assistant, event.content || "", { markdown: true });
    setMessageStatus(assistant, "completed");
    announce("助手回复已完成");
    state.currentAssistant = null;
  } else if (event.type === "message_status") {
    const assistant = messageById(event.message_id) || state.currentAssistant;
    setMessageStatus(assistant, event.status);
    if (event.status === "pending") showError("该请求正在进行中，请稍候刷新会话。");
    announce(`消息状态：${statusLabel(event.status) || event.status}`);
    state.busy = false;
    state.currentAssistant = null;
    if (event.status === "interrupted") setRunStatus("waiting", "等待确认");
    else if (event.status === "failed") setRunStatus("failed", "失败");
    else if (state.serviceStatus === "ready") setRunStatus("ready", "已就绪");
    updateComposer();
  } else if (event.type === "done") {
    state.busy = false;
    state.currentAssistant = null;
    state.approval = null;
    $("#approval-slot").replaceChildren();
    announce("消息流已结束");
    if (state.serviceStatus === "ready") setRunStatus("ready", "已就绪");
    updateComposer();
    loadConversations({ refreshOnly: true });
  } else if (event.type === "error") {
    const assistant = messageById(event.message_id) || state.currentAssistant;
    setMessageStatus(assistant, "failed");
    showError(event.message || "助手运行失败。请重试。");
    announce("助手运行失败");
    state.busy = false;
    state.currentAssistant = null;
    restoreDraft(context.draft);
    setRunStatus("failed", "失败");
    updateComposer();
  }
  if (followConversation) scrollConversationToBottom();
}

async function consumeStream(path, payload, context = {}, optimisticNodes = []) {
  clearError();
  state.busy = true;
  setRunStatus("processing", "处理中");
  updateComposer();
  const controller = new AbortController();
  state.streamController = controller;
  let started = false;
  let terminal = false;
  try {
    const response = await api(path, { method: "POST", body: JSON.stringify(payload), signal: controller.signal });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const consumeFrame = (frame) => {
      const line = frame.split("\n").find((part) => part.startsWith("data: "));
      if (!line) return;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.type === "message_started") started = true;
        if (["done", "error", "approval_required", "message_status"].includes(event.type)) terminal = true;
        handleEvent(event, context);
      } catch (_) {
        showError("无法解析助手的响应，请重试。");
      }
    };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      frames.forEach(consumeFrame);
    }
    buffer += decoder.decode();
    if (buffer.trim()) consumeFrame(buffer);
    if (!terminal && contextMatches(context)) {
      state.busy = false;
      state.currentAssistant = null;
      updateComposer();
      announce("流连接已结束，但未收到完成状态");
      showError("流连接已结束，但服务器没有返回完成状态。请刷新会话后重试。");
    }
    return { started };
  } catch (error) {
    if (!started && context.generation === state.generation) optimisticNodes.forEach((node) => node?.article?.remove());
    throw error;
  } finally {
    if (state.streamController === controller) state.streamController = null;
  }
}

async function sendMessage(text) {
  const cleanText = String(text || "").trim();
  if (!cleanText || state.busy || state.conversationCreating) return;
  if (!state.contextReady || state.serviceStatus !== "ready") {
    showError("服务仍在准备中，请稍候再发送。");
    return;
  }
  if (!state.projects.length) {
    showError("还没有可用项目，请先创建一个项目。");
    openProjectDialog();
    return;
  }
  const input = $("#message-input");
  const startUserId = state.userId;
  const startTenantId = state.tenantId;
  const startProjectId = state.projectId;
  let conversationId = state.conversationId;
  if (!conversationId) conversationId = await ensureConversation();
  if (!conversationId || startUserId !== state.userId || startTenantId !== state.tenantId || (startProjectId && startProjectId !== state.projectId)) {
    if (!input.value.trim()) {
      input.value = cleanText;
      autoResize();
      updateComposer();
    }
    return;
  }
  input.value = "";
  autoResize();
  const context = {
    generation: state.generation,
    conversationId,
    userId: state.userId,
    tenantId: state.tenantId,
    projectId: state.projectId,
    draft: cleanText,
  };
  const requestId = crypto.randomUUID();
  const userMessage = addMessage("user", { timestamp: new Date().toISOString() });
  setMessageContent(userMessage, cleanText);
  const assistantMessage = addMessage("assistant");
  state.currentAssistant = assistantMessage;
  state.toolNodes.clear();
  state.subagentNodes.clear();
  try {
    await consumeStream(`/api/conversations/${conversationId}/messages`, {
      user_id: context.userId,
      tenant_id: context.tenantId,
      request_id: requestId,
      content: cleanText,
    }, context, [userMessage, assistantMessage]);
  } catch (error) {
    if (error.name !== "AbortError" && contextMatches(context)) {
      showError(error.message);
      state.busy = false;
      state.currentAssistant = null;
      restoreDraft(cleanText);
      setRunStatus("failed", "失败");
      updateComposer();
    }
  }
}

function canUseComposer() {
  return state.contextReady && state.serviceStatus === "ready" && state.projects.length > 0;
}

function updateComposer() {
  const input = $("#message-input");
  const button = $("#send-button");
  const label = $("#send-label");
  if (!input || !button) return;
  const ready = canUseComposer();
  const disabled = !ready || state.busy || state.conversationCreating;
  input.disabled = disabled;
  button.disabled = disabled || !input.value.trim();
  input.placeholder = !state.contextReady
    ? "正在准备工作区…"
    : !state.projects.length
      ? "请先创建一个项目…"
      : state.conversationCreating
        ? "正在准备会话…"
        : state.busy
          ? "助手正在处理，结果会实时出现…"
          : "输入你想聊的事情…";
  if (label) label.textContent = state.conversationCreating ? "准备中" : state.busy ? "处理中" : "发送";
  button.setAttribute("aria-busy", String(state.busy || state.conversationCreating));
}

function autoResize() {
  const input = $("#message-input");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function focusableIn(node) {
  return [...node.querySelectorAll("button, [href], input, select, textarea, summary, [tabindex]:not([tabindex=\"-1\"])")]
    .filter((item) => !item.disabled && item.offsetParent !== null);
}

function openSidebar() {
  if (!isMobileLayout()) return;
  state.previousFocus = document.activeElement;
  $("#app-shell").classList.add("sidebar-is-open");
  $("#sidebar-overlay").hidden = false;
  window.setTimeout(() => $("#sidebar-close")?.focus(), 0);
}

function closeSidebar({ restoreFocus = true } = {}) {
  $("#app-shell").classList.remove("sidebar-is-open");
  $("#sidebar-overlay").hidden = true;
  if (restoreFocus && isMobileLayout() && state.previousFocus instanceof HTMLElement) state.previousFocus.focus();
}

function handleSidebarKeydown(event) {
  if (!$("#app-shell").classList.contains("sidebar-is-open")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeSidebar();
    return;
  }
  if (event.key !== "Tab") return;
  const sidebar = $("#sidebar");
  const focusable = focusableIn(sidebar);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage($("#message-input").value);
});
$("#message-input").addEventListener("input", () => {
  autoResize();
  updateComposer();
});
$("#message-input").addEventListener("compositionstart", () => { state.composing = true; });
$("#message-input").addEventListener("compositionend", () => { state.composing = false; });
$("#message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !state.composing) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#new-session").addEventListener("click", () => createConversation());
$("#load-more").addEventListener("click", () => loadConversations({ append: true }));

async function handleUserChange(userId) {
  abortActiveRequests();
  state.generation += 1;
  const previousUserId = state.userId;
  const previousTenantId = state.tenantId;
  const selectedUser = state.users.find((item) => item.user_id === userId);
  state.userId = selectedUser?.user_id || "";
  const tenantIds = selectedUser?.tenant_ids || (selectedUser?.tenant_id ? [selectedUser.tenant_id] : []);
  state.tenantId = tenantIds.includes(previousTenantId) ? previousTenantId : selectedUser?.default_tenant_id || tenantIds[0] || "";
  const userChanged = previousUserId !== state.userId;
  const tenantChanged = userChanged || previousTenantId !== state.tenantId;
  state.contextReady = false;
  if (tenantChanged) {
    state.projectId = "";
    state.conversationId = null;
    state.conversations = [];
    state.conversationCursor = null;
  }
  localStorage.setItem(USER_STORAGE_KEY, state.userId);
  localStorage.setItem(TENANT_STORAGE_KEY, state.tenantId);
  clearConversationView(userChanged ? "正在切换用户" : "正在切换租户", userChanged ? "正在加载该用户的 Project…" : "正在加载新的租户运行上下文…");
  renderProjectList();
  renderConversationPanel();
  renderConversationList();
  updateComposer();
  try {
    await loadProjects();
    await loadConversations();
  } catch (error) {
    showError(error.message);
  }
  renderUserPicker();
}

$("#user-select").addEventListener("click", () => {
  const trigger = $("#user-select");
  setUserPickerOpen(trigger.getAttribute("aria-expanded") !== "true");
});
$("#user-select").addEventListener("keydown", (event) => {
  if (["Enter", " ", "ArrowDown"].includes(event.key)) {
    event.preventDefault();
    setUserPickerOpen(true);
    $("#user-options")?.querySelector(".user-option")?.focus();
  } else if (event.key === "Escape") {
    setUserPickerOpen(false);
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".user-select-wrap")) setUserPickerOpen(false);
});

$("#new-project").addEventListener("click", openProjectDialog);
$("#project-dialog-close").addEventListener("click", closeProjectDialog);
$("#project-dialog-cancel").addEventListener("click", closeProjectDialog);
$("#project-dialog-form").addEventListener("submit", (event) => {
  event.preventDefault();
  createProject($("#project-name-input").value);
});
$("#project-dialog").addEventListener("cancel", () => closeProjectDialog());

$("#conversation").addEventListener("click", (event) => {
  const promptButton = event.target.closest("[data-prompt]");
  if (promptButton) {
    $("#message-input").value = promptButton.dataset.prompt || "";
    autoResize();
    updateComposer();
    $("#message-input").focus();
    return;
  }
  const copyButton = event.target.closest(".copy-button, [data-code-copy]");
  if (copyButton) handleCopyClick(copyButton);
});

$("#mobile-menu").addEventListener("click", openSidebar);
$("#sidebar-close").addEventListener("click", () => closeSidebar());
$("#sidebar-overlay").addEventListener("click", () => closeSidebar());
function updateSidebarCollapseButton(collapsed) {
  const button = $("#sidebar-collapse");
  if (!button) return;
  setIcon(button.querySelector(".icon"), collapsed ? "chevron-right" : "chevron-left");
  button.setAttribute("aria-label", collapsed ? "展开侧栏" : "收起侧栏");
  button.title = collapsed ? "展开侧栏" : "收起侧栏";
}
$("#sidebar-collapse").addEventListener("click", () => {
  const shell = $("#app-shell");
  const collapsed = shell.classList.toggle("is-collapsed");
  localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed));
  updateSidebarCollapseButton(collapsed);
});
document.addEventListener("keydown", (event) => {
  if ($("#app-shell").classList.contains("sidebar-is-open")) {
    handleSidebarKeydown(event);
    if (event.key === "Escape") return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    createConversation();
  }
});
window.addEventListener("resize", () => {
  if (!isMobileLayout()) closeSidebar({ restoreFocus: false });
});

if (localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true" && !isMobileLayout()) {
  $("#app-shell").classList.add("is-collapsed");
}
updateSidebarCollapseButton($("#app-shell").classList.contains("is-collapsed"));
updateProjectContext();
updateComposer();
refreshStatus();
