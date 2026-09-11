import { Button, Drawer, Empty } from "antd";
import Conversations from "@ant-design/x/es/conversations";
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
  const canCreate =
    session.contextReady &&
    session.status?.status === "ready" &&
    !session.conversationCreating;

  const conversationClick = (id: string) => {
    session.selectConversation(id);
    onSelectConversationCloseMobile?.();
  };

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
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="还没有可用项目"
            />
          ) : (
            <div className="project-list">
              {session.projects.map((project) => (
                <button
                  key={project.id}
                  type="button"
                  className={[
                    "project-item",
                    project.id === session.projectId ? "is-active" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => void session.openProject(project.id)}
                >
                  <Icon name="folder" size={15} />
                  <span className="project-item-name">{project.name}</span>
                  {project.is_default ? (
                    <span className="project-item-badge">默认</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </section>

        {session.projectId ? (
          <section className="conversation-section" aria-label="当前项目会话">
            <div className="conversation-heading">
              <div className="section-label">当前项目</div>
              <div className="current-project-name">
                {session.projects.find((p) => p.id === session.projectId)?.name ??
                  "未选择项目"}
              </div>
            </div>
            {session.conversations.length > 0 ? (
              <Conversations
                rootClassName="conversation-list"
                activeKey={session.conversationId ?? undefined}
                items={session.conversations.map((conversation) => ({
                  key: conversation.id,
                  label: <><span className="conversation-item-title">{conversation.title || "未命名会话"}</span><span className="conversation-item-time">{formatConversationTime(conversation.updated_at)}</span></>,
                  icon: <Icon name="message-circle" size={14} />,
                }))}
                onActiveChange={(id) => conversationClick(id)}
              />
            ) : (
              <div className="conversation-empty">
                {session.contextReady ? "这个项目还没有会话" : "会话加载中…"}
              </div>
            )}
            {session.conversationCursor ? (
              <Button
                type="text"
                block
                size="small"
                className="load-more"
                onClick={() => void session.loadMoreConversations()}
              >
                加载更多会话
              </Button>
            ) : null}
          </section>
        ) : null}
      </div>

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
