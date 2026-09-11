import { Dropdown } from "antd";

import { Icon } from "./Icon";
import { avatarStyle, userInitials } from "../lib/format";
import { useSession } from "../state/session";

/** 模拟用户选择器：开发入口，非生产认证；租户由用户配置自动推导。 */
export function UserPicker() {
  const session = useSession();
  const current =
    session.users.find((user) => user.user_id === session.userId) ?? null;
  const items = session.users.map((user) => ({
    key: user.user_id,
    label: (
      <span className="user-option">
        <span className="user-avatar" style={avatarStyle(user.user_id)}>
          {userInitials(user.display_name || user.username, user.user_id)}
        </span>
        <span className="user-option-copy">
          <span className="user-option-name">
            {user.display_name || user.username || user.user_id}
          </span>
        </span>
      </span>
    ),
  }));

  return (
    <div className="user-picker">
      <div className="user-picker-label">模拟用户</div>
      <Dropdown
        trigger={["click"]}
        placement="topLeft"
        menu={{
          items,
          selectable: true,
          selectedKeys: session.userId ? [session.userId] : [],
          onClick: ({ key }) => void session.changeUser(String(key)),
        }}
        disabled={session.users.length === 0}
      >
        <button
          type="button"
          className="user-trigger"
          aria-label="选择模拟用户"
        >
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
        </button>
      </Dropdown>
    </div>
  );
}
