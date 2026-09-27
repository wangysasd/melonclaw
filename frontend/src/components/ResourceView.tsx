import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App as AntdApp, Button, Dropdown, Input, Modal, Switch, Tabs } from "antd";

import {
  cancelSkillImport,
  confirmSkillImport,
  createMcp,
  deleteMcp,
  deleteSkill,
  downloadSkill,
  listManageableSkills,
  listMcp,
  prepareRemoteSkillInstall,
  prepareSkillImport,
  publishSkill,
  updateMcp,
  updateSkill,
  updateSkillGlobalState,
} from "../api/client";
import type { ManageableSkill, McpServer, SkillImportDraft, SkillOption } from "../types/api";
import { useSession } from "../state/session";
import { Icon } from "./Icon";
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
 * 插件管理区：占据侧栏之外的整个聊天区域，管理 Skills、MCP 服务与自定义模型。
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
    <main className="resource-view" aria-label="插件">
      <header className="resource-view-header">
        <div className="resource-view-title">
          <Icon name="plug" size={16} />
          <h1>插件</h1>
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

/* ---------- Skills ---------- */

interface ManagerProps {
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  onChanged?: () => void;
}

interface SkillManagerProps extends ManagerProps {
  onTrySkill?: (skill: SkillOption) => void;
}

/**
 * 磁盘上读不出这个技能时的标记文案。
 *
 * 两个状态要分开说：目录没了只能删，目录还在但文件坏了修好就能用——
 * 都写成「目录已丢失」会把用户引去删一个其实还在的技能。
 */
const SKILL_AVAILABILITY_LABELS = {
  missing: "目录已丢失",
  invalid: "技能文件异常",
} as const;

/** 行还在、磁盘上读不出来：不显示在选择器里，只能靠这里发现。 */
function skillUnavailable(skill: ManageableSkill): "missing" | "invalid" | null {
  return skill.availability === "missing" || skill.availability === "invalid"
    ? skill.availability
    : null;
}

const SKILL_AVAILABILITY_HINTS = {
  missing: "目录已丢失，该技能不可用。删除这一行后可以重新上传。",
  invalid: "技能文件无法读取，该技能不可用。修复 SKILL.md 后会自动恢复。",
} as const;

function SkillManager({ userId, isAdmin, notify, onChanged, onTrySkill }: SkillManagerProps) {
  const { modal } = AntdApp.useApp();
  const [items, setItems] = useState<ManageableSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<SkillImportDraft | null>(null);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [remoteRepo, setRemoteRepo] = useState("");
  const [detailSkill, setDetailSkill] = useState<ManageableSkill | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (preserveOrder = false) => {
    setLoading(true);
    try {
      const data = await listManageableSkills({ userId });
      const sorted = [...data.items].sort((a, b) => {
        const aEnabled = a.enabled && (a.user_enabled ?? true);
        const bEnabled = b.enabled && (b.user_enabled ?? true);
        if (aEnabled !== bEnabled) return aEnabled ? -1 : 1;
        return a.display_name.localeCompare(b.display_name, "zh-CN");
      });
      setItems((current) => {
        if (!preserveOrder) return sorted;
        const remaining = new Map(sorted.map((skill) => [skill.name, skill]));
        const retained = current.flatMap((skill) => {
          const updated = remaining.get(skill.name);
          remaining.delete(skill.name);
          return updated ? [updated] : [];
        });
        return [...retained, ...remaining.values()];
      });
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
    async (action: () => Promise<unknown>, successText: string, preserveOrder = false) => {
      try {
        await action();
        notify.success(successText);
        await reload(preserveOrder);
        onChanged?.();
      } catch (error) {
        notify.error(error instanceof Error ? error.message : String(error));
      }
    },
    [notify, reload, onChanged],
  );

  const handleUpload = async (file: File) => {
    try {
      const draft = await prepareSkillImport({ userId, file });
      setPending(draft);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    }
  };

  const handleRemoteInstall = () => {
    const repo = remoteRepo.trim();
    if (!repo) return;
    void runAction(async () => {
      setPending(await prepareRemoteSkillInstall({ userId, repo }));
      setRemoteOpen(false);
      setRemoteRepo("");
    }, "");
  };

  const sourceLabel = (sourceType: string) => {
    if (sourceType === "builtin") return "系统内置";
    if (sourceType === "remote") return "远程安装";
    return "上传";
  };

  const groups = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const filtered = keyword
      ? items.filter((skill) =>
          [skill.display_name, skill.name, skill.description]
            .filter((text): text is string => Boolean(text))
            .some((text) => text.toLowerCase().includes(keyword)),
        )
      : items;
    return (["user", "tenant", "global"] as const)
      .map((scope) => ({
        scope,
        label: scope === "user" ? "我的" : scope === "tenant" ? "租户" : "系统内置",
        items: filtered.filter((skill) => skill.scope === scope),
      }))
      .filter((group) => group.items.length > 0);
  }, [items, search]);

  return (
    <div className="resource-section">
      <div className="resource-actions skill-resource-actions">
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void handleUpload(file);
          }}
        />
        <Input
          allowClear
          placeholder="搜索技能..."
          prefix={<Icon name="search" size={14} />}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="skill-search"
        />
        <div className="skill-actions-trailing">
          <Button icon={<Icon name="blocks" size={14} />} onClick={() => setRemoteOpen(true)}>
            远程安装
          </Button>
          <Button
            type="primary"
            icon={<Icon name="upload" size={14} />}
            onClick={() => fileInputRef.current?.click()}
          >
            上传 Skill
          </Button>
          <Button
            icon={<Icon name="refresh-cw" size={14} />}
            loading={loading}
            aria-label="刷新技能列表"
            onClick={() => void reload()}
          />
        </div>
      </div>

      <div className="skill-groups" aria-busy={loading}>
        {groups.map((group) => (
          <section key={group.scope} className="skill-group">
            <h2 className="skill-group-title">{group.label}</h2>
            <div className="skill-card-grid">
              {group.items.map((skill) => {
                const unavailable = skillUnavailable(skill);
                const isAdded = skill.enabled && (skill.user_enabled ?? true);
                const isUsable = isAdded && unavailable === null;
                const cannotAdd = skill.scope === "global" && !skill.enabled && !isAdmin;
                const canManageContent =
                  skill.scope === "global"
                    ? isAdmin
                    : skill.created_by === userId;
                const menuItems = [
                  {
                    key: "download",
                    label: (
                      <span className="skill-card-menu-label">
                        <Icon name="arrow-up" size={16} rotate={180} />
                        下载
                      </span>
                    ),
                    disabled: unavailable === "missing",
                  },
                  {
                    key: isUsable ? "uninstall" : "delete",
                    label: (
                      <span className="skill-card-menu-label">
                        <Icon name={isUsable ? "x" : "trash-2"} size={16} />
                        {isUsable ? "卸载" : "删除"}
                      </span>
                    ),
                    danger: !isUsable,
                    disabled: !isUsable && !canManageContent,
                  },
                ];
                return (
                  <article
                    key={skill.name}
                    className={unavailable ? "skill-card is-broken" : "skill-card"}
                  >
                    <div className="skill-card-top-actions">
                      <Button
                        className={`skill-card-primary-action ${isAdded ? "is-use" : "is-add"}`}
                        icon={
                          <Icon
                            name={isAdded ? "message-circle-plus" : "plus"}
                            size={12}
                          />
                        }
                        aria-label={
                          isAdded
                            ? `使用技能：${skill.display_name}`
                            : `添加技能：${skill.display_name}`
                        }
                        title={cannotAdd ? "该技能已由管理员停用" : undefined}
                        disabled={
                          unavailable !== null || cannotAdd || (isAdded && !onTrySkill)
                        }
                        onClick={() => {
                          if (isAdded) {
                            onTrySkill?.({
                              id: skill.name,
                              display_name: skill.display_name,
                              description: skill.description,
                              scope: skill.scope,
                            });
                            return;
                          }
                          void runAction(async () => {
                            if (skill.scope === "global" && isAdmin && !skill.enabled) {
                              await updateSkillGlobalState(skill.name, { userId, enabled: true });
                              if (skill.user_enabled === false) {
                                await updateSkill(skill.name, { userId, enabled: true });
                              }
                              return;
                            }
                            await updateSkill(skill.name, { userId, enabled: true });
                          }, "已添加", true);
                        }}
                      >
                        {isAdded ? "使用" : "添加"}
                      </Button>
                      <Dropdown
                        classNames={{ root: "skill-card-menu" }}
                        trigger={["click"]}
                        menu={{
                          items: menuItems,
                          onClick: ({ key }) => {
                            if (key === "download") {
                              void downloadSkill(skill.name, { userId })
                                .then(() => notify.success("技能包已开始下载"))
                                .catch((error) =>
                                  notify.error(error instanceof Error ? error.message : String(error)),
                                );
                            }
                            if (key === "uninstall") {
                              void runAction(
                                () => updateSkill(skill.name, { userId, enabled: false }),
                                "已卸载",
                                true,
                              );
                            }
                            if (key === "delete") {
                              modal.confirm({
                                title: "删除技能？",
                                content: `删除「${skill.display_name}」及其文件后无法恢复。`,
                                okText: "删除",
                                cancelText: "取消",
                                okButtonProps: { danger: true },
                                onOk: () =>
                                  runAction(
                                    () => deleteSkill(skill.name, { userId }),
                                    "已删除",
                                  ),
                              });
                            }
                          },
                        }}
                      >
                        <Button
                          type="text"
                          className="skill-card-icon-action skill-card-more"
                          aria-label={`更多技能操作：${skill.display_name}`}
                          title="更多操作"
                        >
                          …
                        </Button>
                      </Dropdown>
                    </div>
                    <div
                      className="skill-card-content"
                      role="button"
                      tabIndex={0}
                      aria-haspopup="dialog"
                      aria-label={`查看技能详情：${skill.display_name}`}
                      onClick={() => setDetailSkill(skill)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setDetailSkill(skill);
                        }
                      }}
                    >
                      <header className="skill-card-head">
                        <span className="skill-card-name" title={skill.display_name}>
                          {skill.display_name}
                        </span>
                        {unavailable ? (
                          <span className="resource-badge is-broken">
                            {SKILL_AVAILABILITY_LABELS[unavailable]}
                          </span>
                        ) : null}
                      </header>
                      <p className="skill-card-desc">
                        {unavailable
                          ? SKILL_AVAILABILITY_HINTS[unavailable]
                          : skill.description || skill.name}
                      </p>
                      <span className="skill-card-meta">
                        {sourceLabel(skill.source_type)}
                      </span>
                    </div>
                    {skill.scope === "user" && isAdmin ? (
                      <footer className="skill-card-actions">
                        <Button
                          size="small"
                          disabled={unavailable !== null}
                          onClick={() =>
                            void runAction(
                              () => publishSkill(skill.name, { userId }),
                              "已发布为全局共享",
                            )
                          }
                        >
                          发布
                        </Button>
                      </footer>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </section>
        ))}
        {!loading && groups.length === 0 ? (
          <p className="resource-empty">
            {items.length === 0 ? "还没有 Skill，点击右上角「上传 Skill」开始。" : "没有匹配的技能。"}
          </p>
        ) : null}
      </div>

      <Modal
        open={detailSkill !== null}
        title={detailSkill?.display_name}
        footer={null}
        onCancel={() => setDetailSkill(null)}
        width={560}
      >
        {detailSkill ? (
          <div className="skill-detail-modal-content">
            <span className="skill-card-meta">
              {sourceLabel(detailSkill.source_type)}
            </span>
            <p>{detailSkill.description || detailSkill.name}</p>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={remoteOpen}
        title="远程安装 Skill"
        okText="安装"
        cancelText="取消"
        onCancel={() => {
          setRemoteOpen(false);
          setRemoteRepo("");
        }}
        onOk={handleRemoteInstall}
      >
        <p className="resource-hint">输入 GitHub 仓库地址（owner/repo），从远端安装为 Skill。</p>
        <Input
          placeholder="owner/repo"
          value={remoteRepo}
          onChange={(event) => setRemoteRepo(event.target.value)}
          onPressEnter={handleRemoteInstall}
        />
      </Modal>

      <Modal
        open={pending !== null}
        onCancel={() => {
          if (pending) void cancelSkillImport({ userId, draftId: pending.draft_id });
          setPending(null);
        }}
        onOk={() => {
          if (!pending) return;
          void runAction(async () => {
            await confirmSkillImport({ userId, draftId: pending.draft_id });
            setPending(null);
          }, "安装完成");
        }}
        okText="确认安装"
        cancelText="取消"
        title="确认安装 Skill"
      >
        {pending ? (
          <div className="resource-draft">
            <p>
              <strong>{pending.display_name}</strong>（{pending.name}）
            </p>
            <p>{pending.description}</p>
            <p className="resource-draft-meta">包含 {pending.file_count} 个文件</p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
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
