import { Dropdown, Tooltip } from "antd";
import { useState } from "react";
import { useSession } from "../state/session";
import { avatarStyle, userInitials } from "../lib/format";
import { Icon } from "./Icon";
import { UserPicker } from "./UserPicker";
import { ToolCatalogDialog } from "./ToolCatalogDialog";

export type WorkspaceView = "chat" | "resources" | "amp" | "mindera";

export function GlobalNav({ view, onHome, onResources, onAmp, onMindera }: {
  view: WorkspaceView;
  onHome: () => void;
  onResources: () => void;
  onAmp: () => void;
  onMindera: () => void;
}) {
  const session = useSession();
  const [statusOpen, setStatusOpen] = useState(false);
  const current = session.users.find((user) => user.user_id === session.userId);
  const fullName = current?.display_name || current?.username || session.userId || "正在加载用户…";
  const ready = session.status?.status === "ready";
  const statusLabel = !session.status ? "系统状态：加载中" : ready ? "系统状态：就绪" : "系统状态：需要检查配置";
  return (
    <>
      <nav className="global-nav" aria-label="全局导航">
        <Tooltip title="Home" placement="right" trigger={["hover", "focus"]}>
          <button className={`global-nav-button${view === "chat" ? " is-active" : ""}`} aria-label="Home" aria-current={view === "chat" ? "page" : undefined} onClick={onHome}>
            <Icon name={view === "chat" ? "home-filled" : "home"} size={20} />
          </button>
        </Tooltip>
        <Tooltip title="拓展" placement="right" trigger={["hover", "focus"]}>
          <button className={`global-nav-button${view === "resources" ? " is-active" : ""}`} aria-label="拓展" aria-current={view === "resources" ? "page" : undefined} onClick={onResources}>
            {view === "resources" ? <img src="/assets/icons/blocks-filled.svg" width={20} height={20} alt="" aria-hidden="true" /> : <Icon name="blocks" size={20} />}
          </button>
        </Tooltip>
        <Tooltip title="AMP" placement="right" trigger={["hover", "focus"]}>
          <button className={`global-nav-button global-nav-letter${view === "amp" ? " is-active" : ""}`} aria-label="AMP" aria-current={view === "amp" ? "page" : undefined} onClick={onAmp}>A</button>
        </Tooltip>
        <Tooltip title="Mindera" placement="right" trigger={["hover", "focus"]}>
          <button className={`global-nav-button global-nav-letter${view === "mindera" ? " is-active" : ""}`} aria-label="Mindera" aria-current={view === "mindera" ? "page" : undefined} onClick={onMindera}>M</button>
        </Tooltip>
        <div className="global-nav-footer">
          <UserPicker compact />
          <Tooltip title={statusLabel} placement="right" trigger={["hover", "focus"]}>
            <button className="global-nav-button" aria-label={statusLabel} aria-haspopup="dialog" aria-expanded={statusOpen} onClick={() => setStatusOpen(true)}>
              <Icon name={ready ? "shield-check" : session.status ? "circle-alert" : "loader-circle"} size={20} />
              <span className={`global-status-dot${ready ? " is-ready" : ""}`} />
            </button>
          </Tooltip>
          <Tooltip title={`当前用户：${fullName}`} placement="right" trigger={["hover", "focus"]}>
            <Dropdown trigger={["click"]} placement="rightBottom" classNames={{ root: "current-user-menu" }} menu={{
              selectable: false,
              items: [
                { key: "name", className: "current-user-menu-profile", label: <span className="current-user-profile">
                  <span className="user-avatar" style={avatarStyle(session.userId)}>{current ? userInitials(fullName, session.userId) : "?"}</span>
                  <span className="current-user-name">{fullName}</span>
                </span> },
                { type: "divider" },
                { key: "logout", icon: <Icon name="log-out" size={16} />, label: "Log out" },
              ],
            }}>
              <button type="button" className="global-nav-button" aria-label={`当前用户：${fullName}`} aria-haspopup="menu">
                <span className="user-avatar" style={avatarStyle(session.userId)}>{current ? userInitials(fullName, session.userId) : "?"}</span>
              </button>
            </Dropdown>
          </Tooltip>
        </div>
      </nav>
      <ToolCatalogDialog open={statusOpen} userId={session.userId} status={session.status} modelOptions={session.modelOptions} onClose={() => setStatusOpen(false)} />
    </>
  );
}
