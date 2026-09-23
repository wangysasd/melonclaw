import { App as AntdApp, Button, Drawer, Dropdown, Input, Modal, type MenuProps } from "antd";
import { useEffect, useState } from "react";

import { Icon } from "./Icon";
import { UserPicker } from "./UserPicker";
import { formatConversationTime } from "../lib/format";
import { useServiceStatus } from "../hooks/useServiceStatus";
import { useSession } from "../state/session";
import { SIDEBAR_STORAGE_KEY, readStorage, writeStorage } from "../state/storage";

function RunDetails() {
  const { status } = useServiceStatus();
  const serviceState = status?.status ?? "starting";
  const model = [status?.provider, status?.model].filter(Boolean).join(":");
  const mcp =
    status && status.mcp_servers.length > 0
      ? `MCP · ${status.mcp_servers.join(" · ")}`
      : status?.database === "connected"
        ? "MCP · 未配置"
        : "综合能力连接中";
  return (
    <details className="runtime-details">
      <summary className="runtime-summary">
        <Icon name="shield-check" size={14} />
        <span>运行详情</span>
        <Icon name="chevron-right" size={15} className="runtime-chevron" />
      </summary>
      <div className="runtime-card">
        <div className="runtime-line">
          <span
            className={[
              "status-dot",
              serviceState === "starting" ? "is-loading" : "",
              serviceState === "error" ? "is-error" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          />
          <span>
            {serviceState === "error"
              ? "启动失败"
              : serviceState === "ready"
                ? "服务已就绪"
                : "正在启动助手"}
          </span>
        </div>
        {status?.message ? (
          <div className="runtime-detail runtime-error-text">{status.message}</div>
        ) : null}
        <div className="runtime-model">{model || "连接配置读取中…"}</div>
        <div className="runtime-detail">{mcp}</div>
      </div>
    </details>
  );
}

type ResourceKind = "project" | "conversation";

function MoreButton({ label, menu }: { label: string; menu: MenuProps }) {
  return (
    <Dropdown menu={menu} trigger={["click"]} placement="bottomRight" classNames={{ root: "sidebar-action-dropdown" }}>
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
  onOpenProjectDialog: () => void;
  onSelectConversationCloseMobile?: () => void;
}

/** 侧栏内容：品牌区、新建对话、项目/会话、用户选择器、运行详情。 */
export function SidebarContent({
  collapsed,
  onToggleCollapse,
  onNewConversation,
  onOpenProjectDialog,
  onSelectConversationCloseMobile,
}: SidebarContentProps) {
  const session = useSession();
  const { modal, message } = AntdApp.useApp();
  const [renaming, setRenaming] = useState<{ kind: ResourceKind; id: string; name: string } | null>(null);
  const [submittingRename, setSubmittingRename] = useState(false);
  // 新建按钮在创建中也不禁用：在途到达的点击由 newConversation 排队补建。
  // 禁掉按钮会让“新建空会话”（前后都是相同欢迎页）看起来像没反应，
  // 用户下意识再点一次反而更容易落在禁用窗口里被吞掉。
  const canCreate =
    session.contextReady &&
    session.status?.status === "ready";

  const conversationClick = (id: string) => {
    session.selectConversation(id);
    onSelectConversationCloseMobile?.();
  };
  const runningIds = session.runningConversationIds ?? [];
  const actionMenu = (kind: ResourceKind, id: string, name: string, pinned: boolean): MenuProps => ({
    items: [
      { key: "pin", label: pinned ? "取消置顶" : "置顶" },
      { key: "rename", label: "重命名" },
      { key: "delete", label: "删除", danger: true },
    ],
    onClick: ({ key, domEvent }) => {
      domEvent.stopPropagation();
      if (key === "pin") {
        void (kind === "project"
          ? session.updateProject(id, { isPinned: !pinned })
          : session.updateConversation(id, { isPinned: !pinned }));
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
            else await session.deleteConversation(id);
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
      if (ok) setRenaming(null);
    } finally {
      setSubmittingRename(false);
    }
  };

  const conversationRows = (items: typeof session.conversations, isRecent: boolean) => (
    <div className="conversation-list">
      {items.map((conversation) => {
        const running = runningIds.includes(conversation.id);
        const title = conversation.title || "未命名会话";
        return (
          <div key={conversation.id} className={["conversation-item", conversation.id === session.conversationId ? "is-active" : ""].filter(Boolean).join(" ")}>
            <button type="button" className="conversation-item-main" onClick={() => isRecent ? (session.openRecent(conversation.id), onSelectConversationCloseMobile?.()) : conversationClick(conversation.id)} title={title}>
              <Icon name={running ? "loader-circle" : "message-circle"} size={14} className={running ? "mc-icon-spin" : undefined} />
              <span className="conversation-item-title">{title}</span>
              {conversation.is_pinned ? <Icon name="pin" size={12} className="sidebar-pinned" /> : null}
              <span className="conversation-item-time">{formatConversationTime(conversation.updated_at)}</span>
            </button>
            <MoreButton label={`对话「${title}」`} menu={actionMenu("conversation", conversation.id, title, Boolean(conversation.is_pinned))} />
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

      <button
        type="button"
        className="new-session"
        onClick={onNewConversation}
        disabled={!canCreate}
      >
        <Icon name="plus" size={16} />
        <span className="new-session-label">新建对话</span>
        <span className="shortcut">⌘ K</span>
      </button>

      <div className="sidebar-scroll">
        <section className="project-section" aria-label="项目">
          <div className="section-heading">
            <div className="section-label">项目</div>
            <button
              type="button"
              className="new-project"
              onClick={onOpenProjectDialog}
              title="新增项目"
            >
              <Icon name="plus" size={14} />
              <span>新增项目</span>
            </button>
          </div>
          {session.projects.length === 0 && session.contextReady ? (
            <Button
              type="dashed"
              block
              className="project-empty-create"
              onClick={onOpenProjectDialog}
            >
              创建新项目
            </Button>
          ) : (
            <div className="project-list">
              {session.projects.map((project) => {
                const expanded = project.id === session.projectId;
                return (
                  <div key={project.id} className="project-group">
                    <div className={["project-item", expanded ? "is-active" : ""].filter(Boolean).join(" ")}>
                      <button type="button" className="project-item-main" onClick={() => void session.openProject(project.id)} title={project.name} aria-expanded={expanded}>
                        <Icon name={expanded ? "folder-open" : "folder"} size={15} />
                        <span className="project-item-name">{project.name}</span>
                        {project.is_pinned ? <Icon name="pin" size={12} className="sidebar-pinned" /> : null}
                      </button>
                      <MoreButton label={`项目「${project.name}」`} menu={actionMenu("project", project.id, project.name, Boolean(project.is_pinned))} />
                      <button
                        type="button"
                        className="project-item-new"
                        onClick={() => void session.newConversation(project.id)}
                        disabled={!canCreate}
                        title={`在「${project.name}」中新建会话`}
                        aria-label={`在「${project.name}」中新建会话`}
                      >
                        <Icon name="message-square-plus" size={15} />
                      </button>
                    </div>
                    {expanded ? (
                      <div className="project-children" aria-label={`${project.name}的对话`}>
                        {session.conversations.length > 0 ? conversationRows(session.conversations, false) : null}
                        {session.conversations.length === 0 && (session.conversationsLoadFailed || session.conversationsLoading) ? (
                          <div className="conversation-empty">{session.conversationsLoadFailed ? "会话加载失败" : "会话加载中…"}</div>
                        ) : null}
                        {session.conversationCursor ? (
                          <Button type="text" block size="small" className="load-more" onClick={() => void session.loadMoreConversations()}>加载更多会话</Button>
                        ) : null}
                        {!session.conversationsLoading && !session.conversationsLoadFailed && session.conversations.length === 0 ? (
                          <button type="button" className="project-child-new" onClick={() => void session.newConversation(project.id)} disabled={!canCreate}>
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
          {session.recents.length > 0 ? (
            conversationRows(session.recents, true)
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
        <RunDetails />
      </div>
    </div>
  );
}

export interface SidebarProps {
  onNewConversation: () => void;
  onOpenProjectDialog: () => void;
}

/** 侧栏容器：桌面折叠态 + 移动端抽屉（≤768px）。 */
export function Sidebar({ onNewConversation, onOpenProjectDialog }: SidebarProps) {
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
          onSelectConversationCloseMobile={() => setMobileOpen(false)}
        />
      </Drawer>
    </>
  );
}
