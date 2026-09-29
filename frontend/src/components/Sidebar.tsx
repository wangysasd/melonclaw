import { App as AntdApp, Button, Drawer, Dropdown, Input, Modal, type MenuProps } from "antd";
import { useEffect, useState } from "react";

import { Icon } from "./Icon";
import { UserPicker } from "./UserPicker";
import { listConversations } from "../api/client";
import { formatConversationTime } from "../lib/format";
import { useSession } from "../state/session";
import { SIDEBAR_STORAGE_KEY, readStorage, writeStorage } from "../state/storage";
import type { ConversationSummary } from "../types/api";

type ResourceKind = "project" | "conversation";

function MoreButton({ label, menu }: { label: string; menu: MenuProps }) {
  return (
    <Dropdown menu={menu} trigger={["click"]} placement="bottomLeft" classNames={{ root: "sidebar-action-dropdown" }}>
      <button type="button" className="sidebar-more" aria-label={`${label}更多操作`} title="更多操作" onClick={(event) => event.stopPropagation()}>
        <span aria-hidden="true">···</span>
      </button>
    </Dropdown>
  );
}

export interface SidebarContentProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNewConversation: () => void;
  onOpenProjectDialog: (moveConversationId?: string) => void;
  onOpenResources: () => void;
  resourcesActive?: boolean;
  onNavigateChat?: () => void;
  onSelectConversationCloseMobile?: () => void;
}

/** 侧栏内容：品牌区、新建对话、拓展、项目/会话、用户选择器。 */
export function SidebarContent({
  collapsed,
  onToggleCollapse,
  onNewConversation,
  onOpenProjectDialog,
  onOpenResources,
  resourcesActive = false,
  onNavigateChat,
  onSelectConversationCloseMobile,
}: SidebarContentProps) {
  const session = useSession();
  const { modal, message } = AntdApp.useApp();
  const [renaming, setRenaming] = useState<{ kind: ResourceKind; id: string; name: string } | null>(null);
  const [submittingRename, setSubmittingRename] = useState(false);
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(session.projectId || null);
  const [projectList, setProjectList] = useState<{
    projectId: string; userId: string;
    items: ConversationSummary[]; cursor: string | null; loading: boolean; failed: boolean;
  } | null>(null);
  const [projectListRefresh, setProjectListRefresh] = useState(0);
  const [movingConversationId, setMovingConversationId] = useState<string | null>(null);
  const {
    projectId: selectedProjectId,
    contextReady,
    projects,
    userId,
    conversationListRevision,
    acknowledgeConversationRows,
  } = session;

  useEffect(() => {
    if (session.projectId) setExpandedProjectId(session.projectId);
  }, [session.projectId]);

  useEffect(() => {
    const projectId = expandedProjectId;
    if (!projectId || projectId === selectedProjectId || !contextReady || !projects.some((project) => project.id === projectId)) return;
    const controller = new AbortController();
    setProjectList({ projectId, userId, items: [], cursor: null, loading: true, failed: false });
    void listConversations({ userId, projectId, limit: 10 }, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setProjectList({ projectId, userId, items: data.items, cursor: data.next_cursor, loading: false, failed: false });
          acknowledgeConversationRows(data.items.map((item) => item.id));
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          message.error(error instanceof Error ? error.message : String(error));
          setProjectList({ projectId, userId, items: [], cursor: null, loading: false, failed: true });
        }
      });
    return () => controller.abort();
  }, [expandedProjectId, selectedProjectId, contextReady, projects, userId, conversationListRevision, acknowledgeConversationRows, projectListRefresh, message]);

  const loadMoreProjectConversations = async (projectId: string, cursor: string) => {
    const userId = session.userId;
    try {
      const data = await listConversations({ userId, projectId, limit: 10, cursor });
      setProjectList((current) => current?.projectId === projectId && current.userId === userId && current.cursor === cursor
        ? { ...current, items: [...current.items, ...data.items], cursor: data.next_cursor }
        : current);
      session.acknowledgeConversationRows(data.items.map((item) => item.id));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };
  // 空白聊天页可直接切换，只有服务尚未就绪时禁用新建入口。
  const canCreate =
    session.contextReady &&
    session.status?.status === "ready";

  const conversationClick = (id: string, projectId?: string) => {
    onNavigateChat?.();
    if (projectId && projectId !== session.projectId) session.openProjectConversation(projectId, id);
    else session.selectConversation(id);
    onSelectConversationCloseMobile?.();
  };
  const runningIds = session.runningConversationIds ?? [];
  const optimisticRows = session.optimisticConversations ?? [];
  const visibleRows = (items: ConversationSummary[], projectId: string | null): (ConversationSummary & { localOnly?: boolean })[] => {
    const listedIds = new Set(items.map((item) => item.id));
    return [...optimisticRows.filter((item) => item.project_id === projectId && !listedIds.has(item.id)), ...items];
  };
  const recentConversations = visibleRows(session.recents, null);
  const actionMenu = (kind: ResourceKind, id: string, name: string, pinned: boolean, ordinary = false): MenuProps => ({
    items: [
      { key: "pin", label: pinned ? "取消置顶" : "置顶", icon: <Icon name="pushpin" size={16} className="sidebar-action-pin" /> },
      { key: "rename", label: "重命名", icon: <Icon name="pencil-line" size={16} /> },
      { key: "delete", label: "删除", icon: <Icon name="trash-2" size={16} />, danger: true },
      ...(ordinary ? [
        { type: "divider" as const },
        {
          key: "move",
          label: "移动到项目",
          icon: <Icon name="folder" size={16} />,
          disabled: runningIds.includes(id) || movingConversationId === id,
          popupClassName: "sidebar-move-project-submenu",
          children: [
            { key: "new-project", label: "新建项目", icon: <Icon name="plus" size={16} /> },
            { type: "divider" as const },
            ...session.projects.map((project) => ({
              key: `project:${project.id}`,
              label: project.name,
              icon: <Icon name="folder" size={16} />,
            })),
          ],
        },
      ] : []),
    ],
    onClick: ({ key, domEvent }) => {
      domEvent.stopPropagation();
      if (ordinary && key === "new-project") {
        onOpenProjectDialog(id);
      } else if (ordinary && key.startsWith("project:")) {
        const targetProjectId = key.slice("project:".length);
        setMovingConversationId(id);
        void session.moveConversationToProject(id, targetProjectId).then((ok) => {
          if (ok) {
            setExpandedProjectId(targetProjectId);
            setProjectListRefresh((value) => value + 1);
          }
        }).finally(() => setMovingConversationId(null));
      } else if (key === "pin") {
        if (kind === "project") void session.updateProject(id, { isPinned: !pinned });
        else void session.updateConversation(id, { isPinned: !pinned }).then((ok) => {
          if (ok) setProjectListRefresh((value) => value + 1);
        });
      } else if (key === "rename") {
        setRenaming({ kind, id, name });
      } else if (key === "delete") {
        modal.confirm({
          title: `删除${kind === "project" ? "项目" : "对话"}「${name}」？`,
          content: kind === "project" ? "项目及其中的对话将从列表中移除。" : "该对话将从列表中移除。",
          okText: "删除",
          okButtonProps: { danger: true },
          cancelText: "取消",
          onOk: async () => {
            if (kind === "project") await session.deleteProject(id);
            else if (await session.deleteConversation(id)) setProjectListRefresh((value) => value + 1);
          },
        });
      }
    },
  });

  const submitRename = async () => {
    if (!renaming || submittingRename) return;
    const name = renaming.name.trim();
    const maxLength = renaming.kind === "project" ? 120 : 200;
    if (!name || name.length > maxLength) {
      message.error(`名称长度须在 1 到 ${maxLength} 个字符之间。`);
      return;
    }
    setSubmittingRename(true);
    try {
      const ok = renaming.kind === "project"
        ? await session.updateProject(renaming.id, { name })
        : await session.updateConversation(renaming.id, { name });
      if (ok) {
        setRenaming(null);
        if (renaming.kind === "conversation") setProjectListRefresh((value) => value + 1);
      }
    } finally {
      setSubmittingRename(false);
    }
  };

  const conversationRows = (items: (ConversationSummary & { localOnly?: boolean })[], isRecent: boolean, projectId?: string) => (
    <div className="conversation-list">
      {items.map((conversation) => {
        const running = runningIds.includes(conversation.id);
        const title = conversation.title || "未命名会话";
        return (
          <div key={conversation.id} className={["conversation-item", conversation.id === session.conversationId ? "is-active" : ""].filter(Boolean).join(" ")}>
            <button type="button" className="conversation-item-main" disabled={conversation.localOnly} onClick={() => {
              if (isRecent) {
                onNavigateChat?.();
                session.openRecent(conversation.id);
                onSelectConversationCloseMobile?.();
              } else conversationClick(conversation.id, projectId);
            }} title={title}>
              <Icon name={running ? "loader-circle" : "message-circle"} size={14} className={running ? "mc-icon-spin" : undefined} />
              <span className="conversation-item-title">{title}</span>
              {conversation.is_pinned ? <Icon name="pin" size={12} className="sidebar-pinned" /> : null}
              <span className="conversation-item-time">{formatConversationTime(conversation.updated_at)}</span>
            </button>
            {!optimisticRows.some((item) => item.id === conversation.id) ? (
              <MoreButton label={`对话「${title}」`} menu={actionMenu("conversation", conversation.id, title, Boolean(conversation.is_pinned), isRecent)} />
            ) : null}
          </div>
        );
      })}
    </div>
  );

  if (collapsed) {
    return (
      <div className="sidebar-content is-collapsed">
        <img
          className="collapsed-brand-mark"
          src="/assets/brand/melonclaw-mark.png"
          alt="MelonClaw"
        />
        <button
          type="button"
          className="icon-button sidebar-expand"
          onClick={onToggleCollapse}
          aria-label="展开侧栏"
          title="展开侧栏"
        >
          <Icon name="chevron-right" size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="sidebar-content">
      <div className="brand-row">
        <div className="brand">
          <img
            className="brand-mark"
            src="/assets/brand/melonclaw-mark.png"
            alt=""
          />
          <img
            className="brand-wordmark"
            src="/assets/brand/melonclaw-word.png"
            alt="MelonClaw"
          />
        </div>
        <button
          type="button"
          className="icon-button sidebar-collapse"
          onClick={onToggleCollapse}
          aria-label="收起侧栏"
          title="收起侧栏"
        >
          <Icon name="chevron-right" size={16} rotate={180} />
        </button>
      </div>

      <div className="sidebar-primary-actions">
        <button
          type="button"
          className="new-session"
          onClick={onNewConversation}
          disabled={!canCreate}
        >
          <Icon name="message-circle-plus" size={16} />
          <span className="new-session-label">新建对话</span>
          <span className="shortcut" aria-hidden="true">
            <kbd>⌘</kbd>
            <kbd>K</kbd>
          </span>
        </button>

        <button
          type="button"
          className={["sidebar-resource-entry", resourcesActive ? "is-active" : ""].filter(Boolean).join(" ")}
          onClick={onOpenResources}
          aria-pressed={resourcesActive}
          title="拓展（Skills、MCP 服务与自定义模型）"
        >
          <Icon name="plug" size={16} />
          <span>拓展</span>
        </button>
      </div>

      <div className="sidebar-scroll">
        <section className="project-section" aria-label="项目">
          <div className="section-heading">
            <div className="section-label">项目</div>
            <button
              type="button"
              className="new-project"
              onClick={() => onOpenProjectDialog()}
              title="新建项目"
            >
              <Icon name="plus" size={14} />
              <span>新建项目</span>
            </button>
          </div>
          {session.projects.length === 0 && session.contextReady ? (
            <Button
              type="dashed"
              block
              className="project-empty-create"
              onClick={() => onOpenProjectDialog()}
            >
              创建新项目
            </Button>
          ) : (
            <div className="project-list">
              {session.projects.map((project) => {
                const expanded = project.id === expandedProjectId;
                const currentList = project.id === session.projectId;
                const remoteList = projectList?.projectId === project.id && projectList.userId === session.userId ? projectList : null;
                const conversations = visibleRows(currentList ? session.conversations : remoteList?.items ?? [], project.id);
                const loading = currentList ? session.conversationsLoading : remoteList?.loading ?? true;
                const failed = currentList ? session.conversationsLoadFailed : remoteList?.failed ?? false;
                const cursor = currentList ? session.conversationCursor : remoteList?.cursor ?? null;
                return (
                  <div key={project.id} className="project-group">
                    <div className="project-item">
                      <button type="button" className="project-item-main" onClick={() => setExpandedProjectId(expanded ? null : project.id)} title={project.name} aria-expanded={expanded}>
                        <Icon name={expanded ? "folder-open" : "folder"} size={15} />
                        <span className="project-item-name">{project.name}</span>
                        {project.is_pinned ? <Icon name="pin" size={12} className="sidebar-pinned" /> : null}
                      </button>
                      <MoreButton label={`项目「${project.name}」`} menu={actionMenu("project", project.id, project.name, Boolean(project.is_pinned))} />
                      <button
                        type="button"
                        className="project-item-new"
                        onClick={() => {
                          onNavigateChat?.();
                          session.startNewConversation(project.id);
                        }}
                        disabled={!canCreate}
                        title={`在「${project.name}」中新建会话`}
                        aria-label={`在「${project.name}」中新建会话`}
                      >
                        <Icon name="message-square-plus" size={15} />
                      </button>
                    </div>
                    {expanded ? (
                      <div className="project-children" aria-label={`${project.name}的对话`}>
                        {conversations.length > 0 ? conversationRows(conversations, false, project.id) : null}
                        {conversations.length === 0 && (failed || loading) ? (
                          <div className="conversation-empty">{failed ? "会话加载失败" : "会话加载中…"}</div>
                        ) : null}
                        {cursor ? (
                          <Button type="text" block size="small" className="load-more" onClick={() => void (currentList ? session.loadMoreConversations() : loadMoreProjectConversations(project.id, cursor))}>加载更多会话</Button>
                        ) : null}
                        {!loading && !failed && conversations.length === 0 ? (
                          <button type="button" className="project-child-new" onClick={() => {
                            onNavigateChat?.();
                            session.startNewConversation(project.id);
                          }} disabled={!canCreate}>
                            <Icon name="plus" size={13} /> 在此项目中新建首个对话
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="conversation-section" aria-label="最近会话">
          <div className="conversation-heading">
            <div className="section-label">最近会话</div>
          </div>
          {recentConversations.length > 0 ? (
            conversationRows(recentConversations, true)
          ) : (
            <div className="conversation-empty">
              {session.contextReady ? "还没有普通会话" : "会话加载中…"}
            </div>
          )}
          {session.recentsCursor ? (
            <Button
              type="text"
              block
              size="small"
              className="load-more"
              onClick={() => void session.loadMoreRecents()}
            >
              加载更多会话
            </Button>
          ) : null}
        </section>
      </div>

      <Modal
        title={renaming?.kind === "project" ? "重命名项目" : "重命名对话"}
        open={renaming !== null}
        onCancel={() => { if (!submittingRename) setRenaming(null); }}
        onOk={() => void submitRename()}
        okText="保存"
        cancelText="取消"
        confirmLoading={submittingRename}
        okButtonProps={{ disabled: !renaming?.name.trim() }}
      >
        <Input
          aria-label="新名称"
          value={renaming?.name ?? ""}
          maxLength={renaming?.kind === "project" ? 120 : 200}
          onChange={(event) => setRenaming((current) => current ? { ...current, name: event.target.value } : null)}
          onPressEnter={() => void submitRename()}
          autoFocus
        />
      </Modal>

      <div className="sidebar-footer">
        <UserPicker />
      </div>
    </div>
  );
}

export interface SidebarProps {
  onNewConversation: () => void;
  onOpenProjectDialog: (moveConversationId?: string) => void;
  onOpenResources: () => void;
  resourcesActive?: boolean;
  onNavigateChat?: () => void;
}

/** 侧栏容器：桌面折叠态 + 移动端抽屉（≤768px）。 */
export function Sidebar({ onNewConversation, onOpenProjectDialog, onOpenResources, resourcesActive, onNavigateChat }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(() =>
    !window.matchMedia("(max-width: 768px)").matches &&
    readStorage(SIDEBAR_STORAGE_KEY) === "true"
      ? true
      : false,
  );
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const onChange = () => {
      if (!media.matches) setMobileOpen(false);
    };
    const onOpenFromHeader = () => setMobileOpen(true);
    media.addEventListener("change", onChange);
    window.addEventListener("melonclaw:open-sidebar", onOpenFromHeader);
    return () => {
      media.removeEventListener("change", onChange);
      window.removeEventListener("melonclaw:open-sidebar", onOpenFromHeader);
    };
  }, []);

  const toggleCollapse = () => {
    setCollapsed((previous) => {
      writeStorage(SIDEBAR_STORAGE_KEY, String(!previous));
      return !previous;
    });
  };

  return (
    <>
      <aside
        className={["sidebar", collapsed ? "is-collapsed" : ""]
          .filter(Boolean)
          .join(" ")}
        aria-label="项目与会话导航"
      >
        <SidebarContent
          collapsed={collapsed}
          onToggleCollapse={toggleCollapse}
          onNewConversation={onNewConversation}
          onOpenProjectDialog={onOpenProjectDialog}
          onOpenResources={onOpenResources}
          resourcesActive={resourcesActive}
          onNavigateChat={onNavigateChat}
        />
      </aside>
      <Drawer
        placement="left"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        className="sidebar-drawer"
        styles={{
          body: { padding: 0, background: "#F5F5F7" },
          wrapper: { width: 280 },
        }}
        title={
          <button
            type="button"
            className="icon-button"
            onClick={() => setMobileOpen(false)}
            aria-label="关闭导航"
          >
            <Icon name="x" size={16} />
          </button>
        }
      >
        <SidebarContent
          collapsed={false}
          onToggleCollapse={toggleCollapse}
          onNewConversation={() => {
            onNewConversation();
            setMobileOpen(false);
          }}
          onOpenProjectDialog={onOpenProjectDialog}
          onOpenResources={() => {
            onOpenResources();
            setMobileOpen(false);
          }}
          resourcesActive={resourcesActive}
          onNavigateChat={onNavigateChat}
          onSelectConversationCloseMobile={() => setMobileOpen(false)}
        />
      </Drawer>
    </>
  );
}
