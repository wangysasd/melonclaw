import { useCallback, useEffect, useState } from "react";
import { App as AntdApp, Button, Input, Switch, Tabs } from "antd";

import {
  createMcp,
  deleteMcp,
  listMcp,
  updateMcp,
} from "../api/client";
import type { McpServer, SkillOption } from "../types/api";
import { useSession } from "../state/session";
import { Icon } from "./Icon";
import { SkillManager } from "./SkillManager";
import { ModelSection } from "./ModelProviders";

const ADMIN_ROLES = new Set(["admin", "owner"]);

/** antd message 实例的最小使用面，便于组件内传参。 */
interface Notify {
  success: (content: string) => void;
  error: (content: string) => void;
}

interface ResourceViewProps {
  onClose: () => void;
  onTrySkill?: (skill: SkillOption) => void;
  /** 打开时默认选中的 TAB（模型选择器「添加自定义模型」跳进来时传 models）。 */
  initialTab?: string;
}

/**
 * 拓展管理区：占据侧栏之外的整个聊天区域，管理 Skills、MCP 服务与自定义模型。
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
    <main className="resource-view" aria-label="拓展">
      <header className="resource-view-header">
        <div className="resource-view-title">
          <Icon name="plug" size={16} />
          <h1>拓展</h1>
        </div>
        <Button icon={<Icon name="x" size={14} />} onClick={onClose}>
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
                <Icon name="book-open" size={14} /> Skills
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
                <Icon name="plug" size={14} /> MCP 服务
              </span>
            ),
            children: (
              <McpManager userId={userId} isAdmin={isAdmin} notify={message} />
            ),
          },
          {
            key: "models",
            label: (
              <span>
                <Icon name="brain" size={14} /> 模型
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

interface ManagerProps {
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  onChanged?: () => void;
}

/* ---------- MCP ---------- */

function McpManager({ userId, isAdmin, notify }: ManagerProps) {
  const [items, setItems] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    slug: "",
    scope: "user",
    transport: "http",
    url: "",
    command: "",
    envText: "",
  });

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listMcp({ userId });
      setItems(data.items);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [userId, notify]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runAction = useCallback(
    async (action: () => Promise<unknown>, successText: string) => {
      try {
        await action();
        notify.success(successText);
        await reload();
      } catch (error) {
        notify.error(error instanceof Error ? error.message : String(error));
      }
    },
    [notify, reload],
  );

  const parseEnv = (text: string): Record<string, string> => {
    const env: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const index = line.indexOf("=");
      if (index > 0) env[line.slice(0, index).trim()] = line.slice(index + 1).trim();
    }
    return env;
  };

  const submitForm = () => {
    const transport = form.transport as "http" | "sse" | "stdio";
    void runAction(async () => {
      await createMcp({
        userId,
        slug: form.slug.trim(),
        scope: form.scope,
        transport,
        url: form.url.trim() || null,
        command: form.command.trim() || null,
        env: parseEnv(form.envText),
      });
      setShowForm(false);
      setForm({ slug: "", scope: "user", transport: "http", url: "", command: "", envText: "" });
    }, "已创建");
  };

  return (
    <div className="resource-section">
      <div className="resource-actions">
        <Button icon={<Icon name="plus" size={14} />} onClick={() => setShowForm((current) => !current)}>
          新建 MCP 服务
        </Button>
        <span className="resource-hint">用户级 MCP 仅支持 http/sse</span>
      </div>

      {showForm ? (
        <div className="resource-form">
          <Input
            placeholder="标识（如 my-tools）"
            value={form.slug}
            onChange={(event) => setForm((current) => ({ ...current, slug: event.target.value }))}
          />
          <div className="resource-form-row">
            <select
              value={form.scope}
              disabled={!isAdmin}
              onChange={(event) => setForm((current) => ({ ...current, scope: event.target.value }))}
              aria-label="共享范围"
            >
              <option value="user">私有</option>
              <option value="global">全局共享（管理员）</option>
            </select>
            <select
              value={form.transport}
              onChange={(event) => setForm((current) => ({ ...current, transport: event.target.value }))}
              aria-label="transport"
            >
              <option value="http">http</option>
              <option value="sse">sse</option>
              {form.scope === "global" ? <option value="stdio">stdio</option> : null}
            </select>
          </div>
          {form.transport === "stdio" ? (
            <Input
              placeholder="command（如 npx）"
              value={form.command}
              onChange={(event) => setForm((current) => ({ ...current, command: event.target.value }))}
            />
          ) : (
            <Input
              placeholder="URL（如 https://example.com/mcp）"
              value={form.url}
              onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))}
            />
          )}
          <Input.TextArea
            rows={3}
            placeholder={"环境变量（每行一个 KEY=VALUE，私有配置不允许 ${VAR}）"}
            value={form.envText}
            onChange={(event) => setForm((current) => ({ ...current, envText: event.target.value }))}
          />
          <Button type="primary" onClick={submitForm}>
            创建
          </Button>
        </div>
      ) : null}

      <ul className="resource-list" aria-busy={loading}>
        {items.map((server) => (
          <li key={server.slug} className="resource-item">
            <span className={`resource-badge is-${server.scope}`}>
              {server.scope === "global" ? "共享" : "我的"}
            </span>
            <span className="resource-copy">
              <span className="resource-title">{server.slug}</span>
              <span className="resource-description">
                {server.transport}
                {server.url ? ` · ${server.url}` : ""}
                {server.command ? ` · ${server.command}` : ""}
                {server.env_keys.length > 0 ? ` · env: ${server.env_keys.join(", ")}` : ""}
              </span>
            </span>
            <Switch
              size="small"
              checked={server.enabled}
              disabled={server.scope === "global" && !isAdmin}
              onChange={(checked) =>
                void runAction(
                  () => updateMcp(server.slug, { userId, enabled: checked }),
                  checked ? "已启用" : "已停用",
                )
              }
            />
            <Button
              size="small"
              danger
              disabled={server.scope === "global" && !isAdmin}
              onClick={() => void runAction(() => deleteMcp(server.slug, { userId }), "已删除")}
            >
              删除
            </Button>
          </li>
        ))}
        {!loading && items.length === 0 ? (
          <li className="resource-empty">还没有 MCP 服务。</li>
        ) : null}
      </ul>
    </div>
  );
}
