import { useState } from "react";
import { App as AntdApp, Button, Tabs } from "antd";

import type { SkillOption } from "../types/api";
import { McpManager } from "./McpManager";
import { useSession } from "../state/session";
import { Icon } from "./Icon";
import { SkillManager } from "./SkillManager";
import { ModelSection } from "./ModelProviders";

const ADMIN_ROLES = new Set(["admin", "owner"]);

interface ResourceViewProps {
  onClose: () => void;
  onTrySkill?: (skill: SkillOption) => void;
  /** 打开时默认选中的 TAB（模型选择器「添加自定义模型」跳进来时传 models）。 */
  initialTab?: string;
}

/**
 * 技能与连接器管理区：占据侧栏之外的整个聊天区域，也可管理自定义模型。
 * 权限提示：后端对每个操作都做强校验，这里只做展示层隐藏。
 */
export function ResourceView({ onClose, onTrySkill, initialTab = "skills" }: ResourceViewProps) {
  const session = useSession();
  const { message } = AntdApp.useApp();
  const [activeTab, setActiveTab] = useState(initialTab);
  const userId = session.userId;
  const isAdmin = ADMIN_ROLES.has(
    session.users.find((user) => user.user_id === userId)?.tenant_role ?? "",
  );

  return (
    <main className="resource-view" data-active-tab={activeTab} aria-label="技能|连接器">
      <header className="resource-view-header">
        <div className="resource-view-title">
          <Icon name="blocks" size={16} />
          <h1>技能|连接器</h1>
        </div>
        <Button icon={<Icon name="arrow-up" size={14} rotate={-90} />} onClick={onClose}>
          返回聊天
        </Button>
      </header>
      <Tabs
        className="resource-view-tabs"
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "skills",
            label: (
              <span>
                <Icon name="book-open" size={18} style={{ transform: "translateY(3px)" }} /> 技能
              </span>
            ),
            children: (
              <SkillManager
                key={userId}
                userId={userId}
                isAdmin={isAdmin}
                onChanged={() => void session.refreshSkills()}
                onTrySkill={onTrySkill}
                notify={message}
              />
            ),
          },
          {
            key: "mcp",
            label: (
              <span>
                <Icon name="plug" size={18} /> 连接器
              </span>
            ),
            children: (
              <McpManager key={userId} userId={userId} isAdmin={isAdmin} notify={message} />
            ),
          },
          {
            key: "models",
            label: (
              <span>
                <Icon name="brain" size={18} /> 模型
              </span>
            ),
            children: (
              <ModelSection
                userId={userId}
                isAdmin={isAdmin}
                onChanged={() => void session.refreshModels()}
                notify={message}
              />
            ),
          },
        ]}
      />
    </main>
  );
}
