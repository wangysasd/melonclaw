const USER_STORAGE_KEY = "melonclaw.user_id.v2";
const TENANT_STORAGE_KEY = "melonclaw.tenant_id.v1";
const CONVERSATION_STORAGE_PREFIX = "melonclaw.conversation_id.";
const PROJECT_STORAGE_PREFIX = "melonclaw.project_id.";

const state = {
  userId: localStorage.getItem(USER_STORAGE_KEY) || "",
  tenantId: localStorage.getItem(TENANT_STORAGE_KEY) || "",
  projectId: "",
  conversationId: null,
  busy: false,
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
  bootstrapped: false,
};

const $ = (selector) => document.querySelector(selector);

function conversationStorageKey(userId = state.userId) {
  return `${CONVERSATION_STORAGE_PREFIX}${userId}`;
}

function userTenantKey(userId, tenantId) {
  return `${userId}::${tenantId}`;
}

function projectStorageKey(userId = state.userId) {
  return `${PROJECT_STORAGE_PREFIX}${userId}`;
}

function setStatus(status, message = "") {
  const dot = $("#status-dot");
  const text = $("#status-text");
  dot.className = `status-dot ${status === "starting" ? "is-loading" : status === "error" ? "is-error" : ""}`;
  text.textContent = status === "ready" ? "助手已就绪" : status === "error" ? "启动失败" : "正在启动助手";
  if (status === "error") showError(message || "无法启动助手，请检查配置。");
}

function showError(message) {
  const old = $(".system-error");
  if (old) old.remove();
  const node = document.createElement("div");
  node.className = "system-error";
  node.textContent = message;
  $("#conversation").prepend(node);
}

function announce(message) {
  const region = $("#sr-status");
  if (region) region.textContent = message;
}

function isNearConversationBottom() {
  const conversation = $("#conversation");
  return conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 96;
}

function scrollConversationToBottom({ smooth = false } = {}) {
  const conversation = $("#conversation");
  conversation.scrollTo({ top: conversation.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

function clearError() {
  const old = $(".system-error");
  if (old) old.remove();
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
    if (info.model) $("#model-text").textContent = `${info.provider}:${info.model}`;
    if (info.mcp_servers?.length) {
      $("#mcp-text").textContent = `综合服务 · ${info.mcp_servers.join(" · ")}`;
    } else {
      $("#mcp-text").textContent = info.status === "ready" ? "网页查询 · 已连接" : "综合能力连接中";
    }
    if (info.status === "ready" && !state.bootstrapped) {
      state.bootstrapped = true;
      await loadUsers();
      await loadProjects();
      await loadConversations();
    }
    if (info.status === "starting") window.setTimeout(refreshStatus, 1200);
  } catch (error) {
    setStatus("error", error.message);
  }
}

async function loadUsers() {
  const response = await api("/api/dev/users");
  const data = await response.json();
  const select = $("#user-select");
  select.innerHTML = "";
  const users = [...(data.items || [])];
  users.forEach((user) => {
    const option = document.createElement("option");
    option.value = userTenantKey(user.user_id, user.tenant_id);
    option.textContent = `${user.username}-${user.tenant_name}`;
    select.append(option);
  });
  const available = users.map((item) => userTenantKey(item.user_id, item.tenant_id));
  let selected = userTenantKey(state.userId, state.tenantId);
  if (!available.includes(selected)) {
    const first = users[0];
    state.userId = first?.user_id || "";
    state.tenantId = first?.tenant_id || "";
    selected = userTenantKey(state.userId, state.tenantId);
  }
  select.value = selected;
  localStorage.setItem(USER_STORAGE_KEY, state.userId);
  localStorage.setItem(TENANT_STORAGE_KEY, state.tenantId);
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
  if (saved && state.projects.some((project) => project.id === saved)) {
    state.projectId = saved;
  } else {
    state.projectId = "";
  }
  localStorage.setItem(projectStorageKey(), state.projectId);
  renderProjectList();
  renderConversationPanel();
}

function formatConversationTime(value) {
  try {
    return new Date(value).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch (_) {
    return "";
  }
}

function projectById(projectId) {
  return state.projects.find((project) => project.id === projectId) || null;
}

function renderProjectList() {
  const list = $("#project-list");
  list.innerHTML = "";
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
    button.className = `project-item ${project.id === state.projectId ? "is-open" : ""}`;
    button.dataset.projectId = project.id;

    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "project-item-copy";
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = project.name;
    const meta = document.createElement("span");
    meta.className = "project-meta";
    meta.textContent = project.is_default ? "默认工作区" : "项目文件夹";
    copy.append(name, meta);

    const arrow = document.createElement("span");
    arrow.className = "project-arrow";
    arrow.textContent = "›";
    arrow.setAttribute("aria-hidden", "true");

    button.append(icon, copy, arrow);
    button.addEventListener("click", () => openProject(project.id));
    list.append(button);
  });
}

function renderConversationPanel() {
  const panel = $("#conversation-section");
  const project = projectById(state.projectId);
  panel.hidden = !project;
  if (!project) {
    $("#conversation-list").innerHTML = "";
    $("#load-more").hidden = true;
  }
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
  clearConversationView("选择一个项目", "点击左侧项目文件夹，查看其中的会话。");
  updateComposer();
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

function renderConversationList() {
  const list = $("#conversation-list");
  list.innerHTML = "";
  if (!state.conversations.length) {
    const empty = document.createElement("div");
    empty.className = "conversation-empty";
    empty.textContent = "还没有会话，点击上方按钮开始。";
    list.append(empty);
  } else {
    state.conversations.forEach((conversation) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `conversation-item ${conversation.id === state.conversationId ? "is-active" : ""}`;
      button.dataset.conversationId = conversation.id;
      const title = document.createElement("div");
      title.className = "conversation-title";
      title.textContent = conversation.title || "新会话";
      const time = document.createElement("div");
      time.className = "conversation-time";
      time.textContent = `${conversation.project_name || "临时默认"} · ${formatConversationTime(conversation.updated_at)}`;
      button.append(title, time);
      button.addEventListener("click", () => selectConversation(conversation.id));
      list.append(button);
    });
  }
  const more = $("#load-more");
  more.hidden = !state.conversationCursor;
  more.disabled = false;
}

async function loadConversations({ append = false, refreshOnly = false } = {}) {
  if (!state.projectId) {
    state.conversations = [];
    state.conversationCursor = null;
    renderConversationPanel();
    if (!append && !refreshOnly) {
      renderConversationList();
      clearConversationView("选择一个项目", "点击左侧项目文件夹，查看其中的会话。");
      updateComposer();
    }
    return;
  }
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  const cursor = append ? state.conversationCursor : null;
  const query = new URLSearchParams({ user_id: userId, tenant_id: tenantId, limit: "20" });
  query.set("project_id", state.projectId);
  if (cursor) query.set("cursor", cursor);
  const controller = new AbortController();
  state.dataController = controller;
  try {
    const response = await api(`/api/conversations?${query.toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId) return;
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
      clearConversationView("还没有聊天会话", "点击“新建对话”，开始一段新的聊天。");
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
  $("#approval-slot").innerHTML = "";
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

function clearConversationView(title = "选择或新建会话", copy = "选择左侧历史会话，或新建一段新的聊天。") {
  state.currentAssistant = null;
  state.approval = null;
  state.toolNodes.clear();
  state.subagentNodes.clear();
  $("#conversation").innerHTML = "";
  $("#approval-slot").innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-conversation";
  const heading = document.createElement("div");
  heading.className = "empty-conversation-title";
  heading.textContent = title;
  const description = document.createElement("div");
  description.className = "empty-conversation-copy";
  description.textContent = copy;
  empty.append(heading, description);
  $("#conversation").append(empty);
  $("#session-label").textContent = title;
}

async function createConversation() {
  if (state.busy) return;
  if (!state.projectId) {
    const defaultProject = state.projects.find((project) => project.is_default) || state.projects[0];
    if (!defaultProject) {
      showError("项目正在加载，请稍后再试。");
      return;
    }
    state.projectId = defaultProject.id;
    localStorage.setItem(projectStorageKey(), state.projectId);
    renderProjectList();
    renderConversationPanel();
  }
  abortActiveRequests();
  clearError();
  const generation = state.generation;
  const userId = state.userId;
  const tenantId = state.tenantId;
  try {
    const response = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, tenant_id: tenantId, project_id: state.projectId || null }),
    });
    const conversation = await response.json();
    if (generation !== state.generation || userId !== state.userId || tenantId !== state.tenantId) return;
    state.generation += 1;
    state.conversationId = conversation.id;
    localStorage.setItem(conversationStorageKey(), state.conversationId);
    clearConversationView("新会话", "输入第一条消息，开始与助手聊天。");
    updateComposer();
    await loadConversations({ refreshOnly: true });
    renderConversationList();
  } catch (error) {
    showError(error.message);
  }
}

async function createProject() {
  if (state.busy) return;
  const name = window.prompt("Project 名称", "新项目");
  if (name === null || !name.trim()) return;
  clearError();
  try {
    const response = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ user_id: state.userId, tenant_id: state.tenantId, name: name.trim() }),
    });
    const project = await response.json();
    state.projectId = project.id;
    localStorage.setItem(projectStorageKey(), state.projectId);
    await loadProjects();
    state.conversationId = null;
    state.conversations = [];
    state.conversationCursor = null;
    renderProjectList();
    renderConversationPanel();
    clearConversationView(project.name, "Project 已创建；新建的多个对话会共享同一个工作目录。");
    renderConversationList();
    updateComposer();
    await loadConversations();
  } catch (error) {
    showError(error.message);
  }
}

async function selectConversation(conversationId) {
  if (!conversationId) return;
  abortActiveRequests();
  state.generation += 1;
  const generation = state.generation;
  const userId = state.userId;
  state.conversationId = conversationId;
  localStorage.setItem(conversationStorageKey(), conversationId);
  renderConversationList();
  clearConversationView("正在加载会话", "历史消息加载中…");
  updateComposer();
  const controller = new AbortController();
  state.dataController = controller;
  try {
    const query = new URLSearchParams({ user_id: userId, tenant_id: state.tenantId, limit: "50" });
    const response = await api(`/api/conversations/${conversationId}/messages?${query.toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (generation !== state.generation || userId !== state.userId || conversationId !== state.conversationId) return;
    renderHistory(data);
  } catch (error) {
    if (error.name !== "AbortError" && generation === state.generation) showError(error.message);
  } finally {
    if (state.dataController === controller) state.dataController = null;
  }
}

function statusLabel(status) {
  return {
    pending: "进行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "等待审批",
  }[status] || "";
}

function addMessage(kind, { messageId = null, status = null, scroll = true } = {}) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  if (messageId) article.dataset.messageId = messageId;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  const image = document.createElement("img");
  if (kind === "user") {
    image.src = "/static/melon.png";
    image.alt = "melon";
  } else {
    image.src = "/static/melon-claw.png";
    image.alt = "melon-claw";
  }
  avatar.append(image);
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = kind === "user" ? state.userId : "瓜爪智能助手";
  const time = document.createElement("span");
  time.textContent = kind === "user" ? "刚刚" : (statusLabel(status) || "流式响应");
  meta.append(time);
  const body = document.createElement("div");
  body.className = "message-body";
  const tools = document.createElement("div");
  tools.className = "message-tools";
  content.append(meta, body, tools);
  article.append(avatar, content);
  $("#conversation").append(article);
  $(".empty-conversation")?.remove();
  if (scroll) article.scrollIntoView({ behavior: "smooth", block: "end" });
  return { article, body, tools };
}

function setMessageStatus(message, status) {
  if (!message) return;
  const label = message.article.querySelector(".message-meta span");
  if (label) label.textContent = statusLabel(status) || "流式响应";
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

function renderToolCall(event) {
  const assistant = ensureAssistant();
  let card = state.toolNodes.get(event.call_key);
  if (!card) {
    card = document.createElement("details");
    card.className = "tool-card";
    card.open = event.status !== "completed";
    const head = document.createElement("summary");
    head.className = "tool-head";
    const label = document.createElement("span");
    label.className = "tool-label";
    label.textContent = "调用";
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = event.name || "unknown";
    const status = document.createElement("span");
    status.className = "tool-status";
    status.textContent = event.status === "completed" ? "已完成" : "进行中";
    head.append(label, name, status);
    const details = document.createElement("div");
    details.className = "tool-details";
    const args = document.createElement("pre");
    args.className = "tool-args";
    details.append(args);
    const children = document.createElement("div");
    children.className = "tool-children";
    details.append(children);
    card.append(head, details);
    assistant.tools.append(card);
    state.toolNodes.set(event.call_key, card);
  }
  if (event.name) card.querySelector(".tool-name").textContent = event.name;
  if (event.args !== undefined) card.querySelector(".tool-args").textContent = event.args;
  if (event.status === "started") card.querySelector(".tool-status").textContent = "进行中";
}

function renderToolResult(event) {
  const assistant = ensureAssistant();
  const card = state.toolNodes.get(event.call_key) || (() => {
    renderToolCall({ call_key: event.call_key, name: event.name });
    return state.toolNodes.get(event.call_key);
  })();
  card.classList.toggle("is-complete", event.status !== "failed");
  card.classList.toggle("is-failed", event.status === "failed");
  card.querySelector(".tool-status").textContent = event.status === "failed" ? "失败" : "已完成";
  const output = card.querySelector(".tool-output") || document.createElement("pre");
  output.className = "tool-output";
  output.textContent = event.content || "<无文本输出>";
  if (!output.parentElement) card.querySelector(".tool-details").append(output);
  // 工具输出通常很长；完成后默认收起，用户仍可展开查看。
  if (event.status !== "failed") card.open = false;
}

function subagentStatusLabel(status) {
  return status === "failed" ? "失败" : status === "completed" ? "已完成" : "运行中";
}

function renderSubagentStarted(event) {
  const assistant = ensureAssistant();
  let node = state.subagentNodes.get(event.subagent_id);
  if (node) return node;

  const parentKeys = [];
  if (event.parent_call_id) {
    const rawParent = String(event.parent_call_id);
    parentKeys.push(
      rawParent.startsWith("history:") || rawParent.startsWith("id:") ? rawParent : `id:${rawParent}`,
    );
    if (event.parent_subagent_id) {
      const parentToken = rawParent.includes(":id:") ? rawParent.split(":id:").pop() : rawParent.replace(/^id:/, "");
      parentKeys.unshift(`${event.parent_subagent_id}:id:${parentToken}`);
    }
  }
  let parentKey = parentKeys.find((key) => state.toolNodes.has(key)) || parentKeys[0] || null;
  let parent = parentKey ? state.toolNodes.get(parentKey) : null;
  if (!parent && parentKey) {
    renderToolCall({ call_key: parentKey, name: "task", status: "started" });
    parent = state.toolNodes.get(parentKey);
  }
  const card = document.createElement("details");
  card.className = "subagent-card";
  card.open = true;
  card.dataset.subagentId = event.subagent_id || "";
  const head = document.createElement("summary");
  head.className = "subagent-head";
  const icon = document.createElement("span");
  icon.className = "subagent-icon";
  icon.textContent = "↳";
  const name = document.createElement("span");
  name.className = "subagent-name";
  name.textContent = event.subagent_name || "general-purpose";
  const status = document.createElement("span");
  status.className = "subagent-status";
  status.textContent = "运行中 · 0 个工具";
  head.append(icon, name, status);
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
  node.status.textContent = `运行中 · ${node.toolCount} 个工具`;
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
    const scopedParentSubagent = event.parent_subagent_id
      ? `history:${messageId}:${event.parent_subagent_id}`
      : undefined;
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
  $("#conversation").innerHTML = "";
  $("#approval-slot").innerHTML = "";
  state.currentAssistant = null;
  state.approval = null;
  state.toolNodes.clear();
  state.subagentNodes.clear();
  const conversation = data.conversation;
  $("#session-label").textContent = conversation.title || "聊天会话";
  (data.items || []).forEach((item) => {
    const message = addMessage(item.role === "user" ? "user" : "assistant", {
      messageId: item.id,
      status: item.status,
      scroll: false,
    });
    message.body.textContent = item.content || "";
    if (item.role === "assistant") {
      const events = item.display_metadata?.events || [];
      renderStoredTools(message, events, item.id);
      if (item.status !== "completed") setMessageStatus(message, item.status);
    }
  });
  if (!data.items?.length) {
    clearConversationView(conversation.title || "新会话", "输入第一条消息，开始与助手聊天。");
  }
  if (data.pending_approval) {
    state.busy = true;
    renderApproval(data.pending_approval);
  } else {
    state.busy = false;
  }
  updateComposer();
}

function renderApproval(request) {
  state.approval = request;
  const slot = $("#approval-slot");
  slot.innerHTML = "";
  const panel = document.createElement("div");
  panel.className = "approval-panel";
  const title = document.createElement("div");
  title.className = "approval-title";
  title.innerHTML = "<span>⛨</span> 需要人工审批";
  const copy = document.createElement("div");
  copy.className = "approval-copy";
  copy.textContent = "助手已提出敏感操作。请逐项确认；编辑时只能修改参数，不能替换工具名称。";
  panel.append(title, copy);

  (request.actions || []).forEach((action, index) => {
    const row = document.createElement("div");
    row.className = "approval-action";
    const top = document.createElement("div");
    top.className = "approval-action-top";
    const tool = document.createElement("div");
    tool.className = "approval-tool";
    tool.textContent = `${index + 1}. ${action.name}`;
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
    const args = document.createElement("pre");
    args.className = "approval-args";
    args.textContent = action.args || "{}";
    const edit = document.createElement("textarea");
    edit.className = "approval-edit";
    edit.value = action.args || "{}";
    const reject = document.createElement("textarea");
    reject.className = "approval-reject";
    reject.placeholder = "拒绝原因（可选）";
    select.addEventListener("change", () => {
      edit.style.display = select.value === "edit" ? "block" : "none";
      reject.style.display = ["reject", "respond"].includes(select.value) ? "block" : "none";
    });
    row.append(top, description, args, edit, reject);
    panel.append(row);
  });

  const submit = document.createElement("button");
  submit.className = "approval-submit";
  submit.type = "button";
  submit.textContent = "提交决定并继续";
  submit.addEventListener("click", () => submitApproval(panel, request));
  panel.append(submit);
  slot.append(panel);
  slot.scrollIntoView({ behavior: "smooth", block: "end" });
}

async function submitApproval(panel, request) {
  const rows = [...panel.querySelectorAll(".approval-action")];
  try {
    const decisions = rows.map((row, index) => {
      const choice = row.querySelector("select").value;
      if (choice === "approve") return { type: "approve" };
      if (choice === "reject") return { type: "reject", message: row.querySelector(".approval-reject").value };
      if (choice === "respond") return { type: "respond", message: row.querySelector(".approval-reject").value };
      let args;
      try {
        args = JSON.parse(row.querySelector(".approval-edit").value);
      } catch (error) {
        throw new Error(`第 ${index + 1} 项参数不是合法 JSON：${error.message}`);
      }
      if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error(`第 ${index + 1} 项参数必须是 JSON 对象。`);
      return { type: "edit", edited_action: { name: request.actions[index].name, args } };
    });
    panel.querySelector(".approval-submit").disabled = true;
    state.currentAssistant = state.currentAssistant || [...$("#conversation").querySelectorAll("article.assistant")].map((article) => ({ article, body: article.querySelector(".message-body"), tools: article.querySelector(".message-tools") })).pop();
    await consumeStream(`/api/conversations/${state.conversationId}/approval`, {
      user_id: state.userId,
      tenant_id: state.tenantId,
      decisions,
    }, { generation: state.generation, conversationId: state.conversationId, userId: state.userId });
  } catch (error) {
    panel.querySelector(".approval-submit").disabled = false;
    if (error.name !== "AbortError") showError(error.message);
    state.busy = false;
    updateComposer();
  }
}

function handleEvent(event, generation, conversationId, userId) {
  if (generation !== state.generation || conversationId !== state.conversationId || userId !== state.userId) return;
  const followConversation = isNearConversationBottom();
  if (event.type === "message_started") {
    state.currentAssistant = messageById(event.message_id) || state.currentAssistant || addMessage("assistant", { messageId: event.message_id });
    state.currentAssistant.article.dataset.messageId = event.message_id;
    if (event.user_message_id) {
      const userMessage = [...$("#conversation").querySelectorAll("article.user")].at(-1);
      if (userMessage && !userMessage.dataset.messageId) userMessage.dataset.messageId = event.user_message_id;
    }
  } else if (event.type === "text") {
    ensureAssistant().body.textContent += event.text || "";
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
    state.busy = true;
    updateComposer();
  } else if (event.type === "completed") {
    const assistant = messageById(event.message_id) || ensureAssistant(event.message_id);
    assistant.article.dataset.messageId = event.message_id;
    assistant.body.textContent = event.content || "";
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
    updateComposer();
  } else if (event.type === "done") {
    state.busy = false;
    state.currentAssistant = null;
    state.approval = null;
    $("#approval-slot").innerHTML = "";
    announce("消息流已结束");
    updateComposer();
    loadConversations({ refreshOnly: true });
  } else if (event.type === "error") {
    const assistant = messageById(event.message_id) || state.currentAssistant;
    setMessageStatus(assistant, "failed");
    showError(event.message || "助手运行失败。请重试。");
    announce("助手运行失败");
    state.busy = false;
    state.currentAssistant = null;
    updateComposer();
  }
  if (followConversation) scrollConversationToBottom();
}

async function consumeStream(path, payload, { generation, conversationId, userId, optimisticNodes = [] } = {}) {
  clearError();
  state.busy = true;
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
        handleEvent(event, generation, conversationId, userId);
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
    // flush TextDecoder，避免多字节中文恰好跨在最后一个 chunk 时丢失。
    buffer += decoder.decode();
    if (buffer.trim()) consumeFrame(buffer);
    if (!terminal && generation === state.generation && conversationId === state.conversationId) {
      state.busy = false;
      state.currentAssistant = null;
      updateComposer();
      announce("流连接已结束，但未收到完成状态");
      showError("流连接已结束，但服务器没有返回完成状态。请刷新会话后重试。");
    }
    return { started };
  } catch (error) {
    if (!started && generation === state.generation) optimisticNodes.forEach((node) => node?.article?.remove());
    throw error;
  } finally {
    if (state.streamController === controller) state.streamController = null;
  }
}

async function sendMessage(text) {
  if (!state.conversationId || state.busy || !text.trim()) return;
  const cleanText = text.trim();
  $("#message-input").value = "";
  autoResize();
  const generation = state.generation;
  const conversationId = state.conversationId;
  const userId = state.userId;
  const requestId = crypto.randomUUID();
  const userMessage = addMessage("user");
  userMessage.body.textContent = cleanText;
  const assistantMessage = addMessage("assistant");
  state.currentAssistant = assistantMessage;
  state.toolNodes.clear();
  state.subagentNodes.clear();
  try {
    await consumeStream(`/api/conversations/${conversationId}/messages`, {
      user_id: userId,
      tenant_id: state.tenantId,
      request_id: requestId,
      content: cleanText,
    }, { generation, conversationId, userId, optimisticNodes: [userMessage, assistantMessage] });
  } catch (error) {
    if (error.name !== "AbortError" && generation === state.generation) {
      showError(error.message);
      state.busy = false;
      state.currentAssistant = null;
      updateComposer();
    }
  }
}

function updateComposer() {
  $("#send-button").disabled = !state.conversationId || state.busy;
  $("#message-input").disabled = !state.conversationId || state.busy;
  $("#message-input").placeholder = !state.conversationId ? "请先选择或新建会话…" : state.busy ? "助手正在处理，结果会实时出现…" : "输入你想聊的事情…";
}

function autoResize() {
  const input = $("#message-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage($("#message-input").value);
});
$("#message-input").addEventListener("input", autoResize);
$("#message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#new-session").addEventListener("click", createConversation);
$("#load-more").addEventListener("click", () => loadConversations({ append: true }));
$("#user-select").addEventListener("change", async (event) => {
  abortActiveRequests();
  state.generation += 1;
  const previousUserId = state.userId;
  const [userId, tenantId] = event.target.value.split("::");
  state.userId = userId || "";
  state.tenantId = tenantId || "";
  const userChanged = previousUserId !== state.userId;
  if (userChanged) {
    state.conversationId = null;
    state.projectId = "";
    state.projects = [];
    state.conversations = [];
    state.conversationCursor = null;
  }
  localStorage.setItem(USER_STORAGE_KEY, state.userId);
  localStorage.setItem(TENANT_STORAGE_KEY, state.tenantId);
  clearConversationView(
    userChanged ? "正在切换用户" : "正在切换租户标签",
    userChanged ? "正在加载该用户的 Project…" : "Project 和会话归属用户，不随租户标签变化。",
  );
  renderProjectList();
  renderConversationPanel();
  renderConversationList();
  try {
    await loadProjects();
  } catch (error) {
    showError(error.message);
  }
  await loadConversations();
});
$("#new-project").addEventListener("click", createProject);
document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    $("#message-input").value = button.dataset.prompt;
    autoResize();
    $("#message-input").focus();
  });
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    createConversation();
  }
});

$("#user-select").value = userTenantKey(state.userId, state.tenantId);
updateComposer();
refreshStatus();
