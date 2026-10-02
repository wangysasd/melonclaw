import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App as AntdApp, Button, Dropdown, Input, Modal } from "antd";
import { cancelSkillImport, confirmSkillImport, deleteSkill, downloadSkill, listManageableSkills,
  prepareRemoteSkillInstall, prepareSkillImport, updateSkill, updateSkillGlobalState,
  skillDetails, recoverSkills } from "../api/client";
import type { ManageableSkill, SkillImportDraft, SkillOption, SkillContentPreview } from "../types/api";
import { AppLogo } from "./AppLogo";
import { Icon } from "./Icon";
import { SkillPreview } from "./SkillPreview";
interface Notify { success: (text: string) => void; error: (text: string) => void; }

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
  pending: "等待恢复",
} as const;

/** 行还在、磁盘上读不出来：不显示在选择器里，只能靠这里发现。 */
function skillUnavailable(skill: ManageableSkill): "missing" | "invalid" | "pending" | null {
  return skill.availability === "ready" ? null : skill.availability;
}

const SKILL_AVAILABILITY_HINTS = {
  missing: "目录已丢失，可上传更新修复或删除记录。",
  invalid: "技能文件无法读取，可查看诊断并上传更新修复。",
  pending: "内容操作尚未完成，请管理员执行恢复。",
} as const;

export function SkillManager({ userId, isAdmin, notify, onChanged, onTrySkill }: SkillManagerProps) {
  const { modal } = AntdApp.useApp();
  const [items, setItems] = useState<ManageableSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<SkillImportDraft | null>(null);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [remoteRepo, setRemoteRepo] = useState("");
  const [installStage, setInstallStage] = useState<"prepare" | "confirm" | null>(null);
  const installInFlight = useRef(false);
  const remoteInstallAbort = useRef<AbortController | null>(null);
  const [updateTarget, setUpdateTarget] = useState<ManageableSkill | null>(null);
  const [detailContent, setDetailContent] = useState<SkillContentPreview | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailSkill, setDetailSkill] = useState<ManageableSkill | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (preserveOrder = false) => {
    setLoading(true);
    try {
      const data = await listManageableSkills({ userId });
      const sorted = [...data.items].sort((a, b) => {
        const aEnabled = a.effective_enabled;
        const bEnabled = b.effective_enabled;
        if (aEnabled !== bEnabled) return aEnabled ? -1 : 1;
        return a.display_name.localeCompare(b.display_name, "zh-CN");
      });
      setItems((current) => {
        if (!preserveOrder) return sorted;
        const remaining = new Map(sorted.map((skill) => [skill.id, skill]));
        const retained = current.flatMap((skill) => {
          const updated = remaining.get(skill.id);
          remaining.delete(skill.id);
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

  useEffect(
    () => () => {
      remoteInstallAbort.current?.abort();
    },
    [],
  );

  useEffect(() => {
    let current = true;
    setDetailContent(null);
    setDetailError("");
    if (detailSkill) {
      void skillDetails(detailSkill.name, { userId, scope: detailSkill.scope })
        .then((value) => { if (current) setDetailContent(value); })
        .catch((error: unknown) => { if (current) setDetailError(error instanceof Error ? error.message : String(error)); });
    }
    return () => { current = false; };
  }, [detailSkill, userId]);

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
    if (installInFlight.current) return;
    installInFlight.current = true;
    setInstallStage("prepare");
    try {
      const draft = await prepareSkillImport({ userId, file, targetId: updateTarget?.id });
      setPending(draft);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      installInFlight.current = false;
      setInstallStage(null);
    }
  };

  const handleRemoteInstall = async () => {
    const repo = remoteRepo.trim();
    if (!repo || installInFlight.current) return;
    const controller = new AbortController();
    installInFlight.current = true;
    remoteInstallAbort.current = controller;
    setInstallStage("prepare");
    try {
      const draft = await prepareRemoteSkillInstall({ userId, repo, targetId: updateTarget?.id }, controller.signal);
      setPending(draft);
      setRemoteOpen(false);
      setRemoteRepo("");
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        notify.error(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (remoteInstallAbort.current === controller) {
        remoteInstallAbort.current = null;
      }
      installInFlight.current = false;
      setInstallStage(null);
    }
  };

  const cancelRemoteInstall = () => {
    remoteInstallAbort.current?.abort();
    setRemoteOpen(false);
    setRemoteRepo("");
  };

  const handleConfirmInstall = async () => {
    if (!pending || installInFlight.current) return;
    installInFlight.current = true;
    setInstallStage("confirm");
    try {
      await confirmSkillImport({ userId, draftId: pending.draft_id });
      setPending(null);
      notify.success(pending.operation === "update" ? "内容已更新，原有启停和个人偏好保持不变" : "安装完成，请启用后使用");
      setUpdateTarget(null);
      await reload();
      onChanged?.();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      installInFlight.current = false;
      setInstallStage(null);
    }
  };

  const sourceLabel = (sourceType: string) => {
    if (sourceType === "builtin") return "系统内置";
    if (sourceType === "remote") return "远程安装";
    if (sourceType === "generated") return "聊天生成";
    return "上传";
  };

  const groups = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    // 管理员可恢复或删除停用的共享 Skill；普通用户看不到这些卡片。
    const visibleItems = items.filter(
      (skill) => skill.scope !== "global" || skill.enabled || isAdmin,
    );
    const filtered = keyword
      ? visibleItems.filter((skill) =>
          [skill.display_name, skill.name, skill.description]
            .filter((text): text is string => Boolean(text))
            .some((text) => text.toLowerCase().includes(keyword)),
        )
      : visibleItems;
    return (["user", "global"] as const)
      .map((scope) => ({
        scope,
        label: scope === "user" ? "我的" : "系统共享",
        items: filtered.filter((skill) => skill.scope === scope),
      }))
      .filter((group) => group.items.length > 0);
  }, [items, search, isAdmin]);

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
          <Button disabled={installStage !== null} icon={<Icon name="blocks" size={14} />} onClick={() => { setUpdateTarget(null); setRemoteOpen(true); }}>
            远程安装
          </Button>
          <Button
            type="primary"
            disabled={installStage !== null}
            loading={installStage === "prepare" && !remoteOpen}
            icon={<Icon name="upload" size={14} />}
            onClick={() => { setUpdateTarget(null); fileInputRef.current?.click(); }}
          >
            上传 Skill
          </Button>
          {isAdmin ? <Button onClick={() => void runAction(async () => {
            const report = await recoverSkills(userId);
            notify.success(report.summary);
          }, "恢复与索引检查完成")}>恢复与检查</Button> : null}
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
                const isShadowed = skill.shadowed === true;
                const isAdded = skill.personally_enabled;
                const isUsable = skill.effective_enabled;
                const globallyDisabled = skill.scope === "global" && !skill.enabled;
                const cannotAdd = globallyDisabled;
                const apiScope = skill.scope;
                const canManageContent =
                  skill.scope === "global"
                    ? isAdmin
                    : skill.created_by === userId;
                const menuItems = [
                  { key: "personal", label: isAdded ? (isAdmin ? "对我关闭" : "关闭") : (isAdmin ? "对我启用" : "启用"), disabled: unavailable !== null || globallyDisabled },
                  ...(skill.scope === "global" && isAdmin ? [{ key: "global", label: skill.enabled ? "全员关闭" : "全员启用", disabled: unavailable !== null }] : []),
                  {
                    key: "download",
                    label: "下载",
                    disabled: unavailable === "missing",
                  },
                  ...(canManageContent ? [
                    { key: "update", label: "上传更新内容", disabled: installStage !== null },
                    { key: "remote-update", label: "远程更新内容", disabled: installStage !== null },
                    { key: "delete", label: "删除", danger: true },
                  ] : []),
                ];
                return (
                  <article
                    key={skill.id}
                    className={unavailable ? "skill-card skill-resource-card is-broken" : "skill-card skill-resource-card"}
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
                        title={
                          cannotAdd
                            ? "该技能已由管理员停用"
                            : isShadowed
                              ? "已被我的同名私有技能遮蔽，Agent 使用的是我的私有版本"
                              : undefined
                        }
                        disabled={
                          unavailable !== null ||
                          cannotAdd ||
                          isShadowed ||
                          (isAdded && !onTrySkill)
                        }
                        onClick={() => {
                          if (isAdded) {
                            onTrySkill?.({
                              id: skill.selection_id,
                              display_name: skill.display_name,
                              description: skill.description,
                              scope: skill.scope,
                            });
                            return;
                          }
                          void runAction(() => updateSkill(skill.name, { userId, enabled: true, scope: apiScope }), "已对我启用", true);
                        }}
                      >
                        {globallyDisabled ? "全员已关闭" : isShadowed ? "已遮蔽" : isUsable ? "去试试" : (isAdmin ? "对我启用" : "启用")}
                      </Button>
                      <Dropdown
                        classNames={{ root: "skill-card-menu" }}
                        trigger={["click"]}
                        menu={{
                          items: menuItems,
                          onClick: ({ key }) => {
                            if (key === "download") {
                              void downloadSkill(skill.name, { userId, scope: apiScope })
                                .then(() => notify.success("技能包已开始下载"))
                                .catch((error) =>
                                  notify.error(error instanceof Error ? error.message : String(error)),
                                );
                            }
                            if (key === "personal") {
                              void runAction(() => updateSkill(skill.name, { userId, enabled: !isAdded, scope: apiScope }), isAdded ? "已对我关闭" : "已对我启用", true);
                            }
                            if (key === "global") {
                              void runAction(() => updateSkillGlobalState(skill.name, { userId, enabled: !skill.enabled }), skill.enabled ? "已全员关闭" : "已全员启用", true);
                            }
                            if (key === "update" || key === "remote-update") {
                              setUpdateTarget(skill);
                              if (key === "update") fileInputRef.current?.click();
                              else { setRemoteRepo(skill.source_url); setRemoteOpen(true); }
                            }
                            if (key === "delete") {
                              modal.confirm({
                                className: "skill-delete-modal",
                                title: "删除技能？",
                                content: `删除「${skill.display_name}」及其文件后无法恢复。`,
                                okText: "删除",
                                cancelText: "取消",
                                okButtonProps: { danger: true },
                                onOk: () =>
                                  runAction(
                                    () => deleteSkill(skill.name, { userId, scope: apiScope }),
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
                        <AppLogo name={skill.display_name} />
                        <span className="skill-card-name" title={skill.display_name}>
                          {skill.display_name}
                        </span>
                        {unavailable ? (
                          <span className="resource-badge is-broken">
                            {SKILL_AVAILABILITY_LABELS[unavailable]}
                          </span>
                        ) : null}
                        {isShadowed ? (
                          <span className="resource-badge">
                            已被同名私有技能遮蔽
                          </span>
                        ) : null}
                      </header>
                      <p className="skill-card-desc">
                        {unavailable
                          ? skill.diagnostic || SKILL_AVAILABILITY_HINTS[unavailable]
                          : skill.description || skill.name}
                      </p>
                      <span className="skill-card-meta">
                        {sourceLabel(skill.source_type)} · v{skill.version}
                      </span>
                    </div>
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
        width={800}
      >
        {detailSkill ? (
          <div className="skill-detail-modal-content">
            <span className="skill-card-meta">
              {sourceLabel(detailSkill.source_type)}
            </span>
            <p>{detailSkill.description || detailSkill.name}</p>
            <p>{detailSkill.scope === "global" ? "系统共享" : "我的技能"} · v{detailSkill.version}</p>
            {detailSkill.source_url ? <p>来源：{detailSkill.source_url}</p> : null}
            {detailSkill.source_ref ? <p>{detailSkill.source_type === "generated" ? "来源会话" : "Commit"}：{detailSkill.source_ref}</p> : null}
            {detailSkill.diagnostic ? <p role="alert">{detailSkill.diagnostic}</p> : null}
            {detailError ? <p role="alert">{detailError}</p> : detailContent ? <SkillPreview preview={detailContent} /> : <p role="status">正在读取内容…</p>}
          </div>
        ) : null}
      </Modal>

      <Modal
        open={remoteOpen}
        className="remote-skill-modal"
        title={updateTarget ? "远程更新 Skill" : "远程安装 Skill"}
        okText={installStage === "prepare" ? "正在准备安装…" : "安装"}
        okButtonProps={{
          disabled: installStage !== null || !remoteRepo.trim(),
          loading: installStage === "prepare",
        }}
        closable={false}
        keyboard={installStage === null}
        maskClosable={installStage === null}
        cancelText={installStage === "prepare" ? "取消安装" : "取消"}
        onCancel={cancelRemoteInstall}
        onOk={handleRemoteInstall}
      >
        {installStage === "prepare" ? (
          <p role="status">正在下载并校验 Skill，通常需要几秒到两分钟。可点击「取消安装」结束。</p>
        ) : null}
        <div className="remote-skill-guide">
          <p>选择要安装的 GitHub Skill：</p>
          <div className="remote-skill-guide-row">
            <strong>仓库根目录</strong>
            <span>仓库本身是单个 Skill。默认取 main 分支。</span>
            <code>owner/repo</code>
            <code>https://github.com/owner/repo</code>
          </div>
          <div className="remote-skill-guide-row">
            <strong>仓库子目录</strong>
            <span>按链接指定的分支或 commit 下载子目录。</span>
            <code>https://github.com/owner/repo/tree/分支/子目录</code>
          </div>
          <div className="remote-skill-guide-example">
            <strong>示例</strong>
            <code>https://github.com/vercel-labs/agent-skills/tree/main/skills/writing-guidelines</code>
          </div>
        </div>
        <Input
          disabled={installStage !== null}
          value={remoteRepo}
          onChange={(event) => setRemoteRepo(event.target.value)}
          onPressEnter={handleRemoteInstall}
        />
      </Modal>

      <Modal
        open={pending !== null}
        onCancel={() => {
          if (pending && !installInFlight.current) {
            void cancelSkillImport({ userId, draftId: pending.draft_id }).catch((error: unknown) => notify.error(error instanceof Error ? error.message : String(error)));
          }
          setPending(null);
        }}
        onOk={handleConfirmInstall}
        okText={installStage === "confirm" ? "正在提交…" : pending?.operation === "update" ? "确认更新" : "确认安装"}
        okButtonProps={{ disabled: installStage !== null, loading: installStage === "confirm" }}
        cancelButtonProps={{ disabled: installStage !== null }}
        closable
        keyboard={installStage === null}
        maskClosable={installStage === null}
        cancelText="取消"
        title={pending?.operation === "update" ? "确认更新 Skill" : "确认安装 Skill"}
        width={800}
      >
        {installStage === "confirm" ? (
          <p role="status">正在安装 Skill，完成后将自动关闭此弹窗。右上角可关闭，关闭不撤销已提交的安装。</p>
        ) : null}
        {pending ? (
          <div className="resource-draft">
            <p>
              <strong>{pending.display_name}</strong>（{pending.name}）
            </p>
            <p>{pending.description}</p>
            <p className="resource-draft-meta">包含 {pending.file_count} 个文件 · {pending.scope === "global" ? "系统共享" : "我的技能"}</p>
            {pending.source_url ? <p>来源：{pending.source_url}</p> : null}
            {pending.source_ref ? <p>Commit：{pending.source_ref}</p> : null}
            {pending.base_version !== null ? <p>版本：v{pending.base_version} → v{pending.base_version + 1}</p> : null}
            <SkillPreview preview={pending.preview} showDiff={pending.operation === "update"} />
            <p className="resource-draft-meta">
              {pending.operation === "update" ? "更新保留资源身份、启停与个人偏好。" : pending.scope === "global" ? "安装到系统共享，需管理员「全员启用」后开放给所有人。" : "安装到我的技能，点击「对我启用」后启用。"}
            </p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
