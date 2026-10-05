import { useState } from "react";
import { App as AntdApp, Dropdown, Input, Modal, Tooltip } from "antd";

import { Icon } from "./Icon";
import { createDevUser } from "../api/client";
import { avatarStyle, userInitials } from "../lib/format";
import { useSession } from "../state/session";

const ADMIN_ROLES = new Set(["admin", "owner"]);

/** 模拟用户选择器：开发入口，非生产认证；租户由用户配置自动推导。 */
export function UserPicker({ compact = false }: { compact?: boolean }) {
  const session = useSession();
  const { message } = AntdApp.useApp();
  const [creating, setCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ userId: "", userNameZh: "" });
  const current =
    session.users.find((user) => user.user_id === session.userId) ?? null;
  const isAdmin = ADMIN_ROLES.has(current?.tenant_role ?? "");

  const items = [
    ...session.users.map((user) => ({
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
    })),
    ...(isAdmin
      ? [
          { type: "divider" as const },
          {
            key: "__create__",
            label: (
              <span className="user-option">
                <Icon name="plus" size={14} />
                <span className="user-option-copy">
                  <span className="user-option-name">新建用户</span>
                </span>
              </span>
            ),
          },
        ]
      : []),
  ];

  const submitCreate = async () => {
    const userId = form.userId.trim();
    const userNameZh = form.userNameZh.trim();
    if (!userId || !userNameZh) {
      message.error("用户 ID 和显示名称都不能为空。");
      return;
    }
    setSubmitting(true);
    try {
      await createDevUser({ actorUserId: session.userId, userId, userNameZh });
      message.success(`用户 ${userNameZh} 已创建`);
      setCreating(false);
      setForm({ userId: "", userNameZh: "" });
      await session.refreshUsers();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={`user-picker${compact ? " is-compact" : ""}`}>
      {!compact && <div className="user-picker-label">模拟用户</div>}
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
            if (key === "__create__") setCreating(true);
            else void session.changeUser(String(key));
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
      <Modal
        title="新建用户"
        open={creating}
        onCancel={() => {
          if (!submitting) setCreating(false);
        }}
        onOk={() => void submitCreate()}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
      >
        <div className="user-create-form">
          <Input
            aria-label="用户 ID"
            placeholder="用户 ID（小写字母/数字/下划线/连字符）"
            value={form.userId}
            maxLength={64}
            onChange={(event) =>
              setForm((currentForm) => ({ ...currentForm, userId: event.target.value }))
            }
          />
          <Input
            aria-label="显示名称"
            placeholder="显示名称（1-3 个字符）"
            value={form.userNameZh}
            maxLength={3}
            onChange={(event) =>
              setForm((currentForm) => ({ ...currentForm, userNameZh: event.target.value }))
            }
            onPressEnter={() => void submitCreate()}
          />
        </div>
      </Modal>
    </div>
  );
}
