import { useEffect, useState } from "react";
import { getAuthConfig } from "../api/auth";
import { App, Dropdown, Tooltip } from "antd";

import { Icon } from "./Icon";
import { avatarStyle, userInitials } from "../lib/format";
import { useSession } from "../state/session";

export function UserPicker({ compact = false }: { compact?: boolean }) {
  const session = useSession();
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let active = true;
    void getAuthConfig().then((config) => { if (active) setEnabled(config.passwordless); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const { modal } = App.useApp();
  const current =
    session.users.find((user) => user.user_id === session.userId) ?? null;

  const items = session.users.map((user) => ({
    key: user.user_id,
    label: user.display_name || user.username || user.user_id,
  }));

  if (!enabled) return null;

  return (
    <div className={`user-picker${compact ? " is-compact" : ""}`}>
      {!compact && <div className="user-picker-label">当前用户</div>}
      <Tooltip title={compact ? "更换用户" : undefined} placement="right" trigger={["hover", "focus"]}>
      <Dropdown
        classNames={{ root: "user-switch-menu" }}
        trigger={["click"]}
        placement="rightBottom"
        menu={{
          items,
          selectable: true,
          selectedKeys: session.userId ? [session.userId] : [],
          onClick: ({ key }) => {
            if (key === session.userId) return;
            const clean = window.dispatchEvent(new Event("melonclaw-check-draft", { cancelable: true }));
            if (clean) void session.changeUser(String(key));
            else modal.confirm({ title: "切换用户", content: "未发送内容将被清除，是否继续？", okText: "切换", cancelText: "取消", onOk: () => session.changeUser(String(key)) });
          },
        }}
        disabled={session.users.length === 0}
      >
        <button
          type="button"
          className={compact ? "global-nav-button" : "user-trigger"}
          aria-label="更换用户"
        >
          {compact ? <Icon name="user-switch" size={20} /> : <>
          <span
            className="user-avatar"
            style={avatarStyle(session.userId)}
          >
            {current
              ? userInitials(current.display_name || current.username, current.user_id)
              : "?"}
          </span>
          <span className="user-trigger-copy">
            {current
              ? current.display_name || current.username || current.user_id
              : session.userId || "正在加载用户…"}
          </span>
          <Icon name="chevron-down" size={14} />
          </>}
        </button>
      </Dropdown>
      </Tooltip>
    </div>
  );
}
