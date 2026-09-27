import { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Input, Modal, Switch } from "antd";

import {
  createModel,
  createProvider,
  deleteModel,
  deleteMyProviderKey,
  deleteProvider,
  fetchRemoteModels,
  listManageableModels,
  listManageableProviders,
  setMyProviderKey,
  updateModel,
  updateProvider,
} from "../api/client";
import type { ManageableModel, ManageableProvider } from "../types/api";
import { Icon } from "./Icon";
import { getProviderAvatar } from "./providerIcons";

interface Notify {
  success: (content: string) => void;
  error: (content: string) => void;
}

interface ModelSectionProps {
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  onChanged?: () => void;
}

/** 远端模型 id 转本地 model_key：小写 + 非法字符转连字符。 */
function slugifyModelKey(remoteId: string): string {
  const slug = remoteId
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return slug || "model";
}

/**
 * 模型供应商区（学 Yuxi）：先配供应商，再进供应商配模型。
 * - 顶栏：搜索 + 新增供应商（仅 admin）+ 刷新；
 * - 分组：已启用大卡 / 未启用小卡；
 * - 点击卡片：admin 进供应商编辑弹窗，普通用户进“我的 Key”弹窗；
 * - 卡片 footer「管理模型」进模型管理弹窗（含远端拉取一键启用）。
 */
export function ModelSection({ userId, isAdmin, notify, onChanged }: ModelSectionProps) {
  const { modal } = AntdApp.useApp();
  const [providers, setProviders] = useState<ManageableProvider[]>([]);
  const [models, setModels] = useState<ManageableModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editingKey, setEditingKey] = useState<string | "new" | null>(null);
  const [managingKey, setManagingKey] = useState<string | null>(null);
  const [myKeyProvider, setMyKeyProvider] = useState<ManageableProvider | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [providerData, modelData] = await Promise.all([
        listManageableProviders({ userId }),
        listManageableModels({ userId }),
      ]);
      setProviders(providerData.items);
      setModels(modelData.items);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [userId, notify]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const list = keyword
      ? providers.filter((provider) =>
          [provider.display_name, provider.provider_key, provider.base_url]
            .filter(Boolean)
            .some((text) => text.toLowerCase().includes(keyword)),
        )
      : providers;
    return {
      enabled: list.filter((provider) => isAdmin ? provider.enabled : provider.has_my_key),
      disabled: list.filter((provider) => isAdmin ? !provider.enabled : !provider.has_my_key),
    };
  }, [providers, search, isAdmin]);

  const refreshAll = useCallback(async () => {
    await reload();
    onChanged?.();
  }, [reload, onChanged]);

  const openCard = (provider: ManageableProvider) => {
    if (isAdmin) {
      setEditingKey(provider.provider_key);
    } else {
      setMyKeyProvider(provider);
    }
  };

  return (
    <div className="resource-section">
      <div className="resource-actions skill-resource-actions">
        <Input
          allowClear
          placeholder="搜索供应商..."
          prefix={<Icon name="search" size={14} />}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="skill-search"
        />
        <div className="skill-actions-trailing">
          {isAdmin ? <Button
            type="primary"
            icon={<Icon name="plus" size={14} />}
            onClick={() => setEditingKey("new")}
          >
            新增供应商
          </Button> : null}
          <Button
            icon={<Icon name="refresh-cw" size={14} />}
            loading={loading}
            aria-label="刷新供应商列表"
            onClick={() => void reload()}
          />
        </div>
      </div>
      <p className="resource-hint">
        {isAdmin
          ? "你建的供应商全局共享，默认给全员用"
          : "管理员共享的模型可直接使用；已启用仅表示你已配置自己的 API Key"}
      </p>

      <ProviderGroup
        title={`已启用（${filtered.enabled.length}）`}
        providers={filtered.enabled}
        models={models}
        variant="full"
        onOpen={openCard}
        onManage={setManagingKey}
      />
      <ProviderGroup
        title={`未启用（${filtered.disabled.length}）`}
        providers={filtered.disabled}
        models={models}
        variant="mini"
        onOpen={openCard}
        onManage={setManagingKey}
      />
      {!loading && providers.length === 0 ? (
        <p className="resource-empty">还没有模型供应商，请由管理员添加。</p>
      ) : null}

      {editingKey !== null ? <ProviderModal
        key={`${userId}:${editingKey}`}
        providerKey={editingKey}
        providers={providers}
        userId={userId}
        isAdmin={isAdmin}
        notify={notify}
        modal={modal}
        onClose={() => setEditingKey(null)}
        onChanged={() => void refreshAll()}
      /> : null}
      <ModelManageModal
        providerKey={managingKey}
        providers={providers}
        models={models}
        userId={userId}
        isAdmin={isAdmin}
        notify={notify}
        onClose={() => setManagingKey(null)}
        onChanged={() => void refreshAll()}
      />
      {myKeyProvider ? (
        <MyKeyModal
          key={`${userId}:${myKeyProvider.provider_key}`}
          provider={myKeyProvider}
          userId={userId}
          notify={notify}
          onClose={() => setMyKeyProvider(null)}
          onChanged={() => void refreshAll()}
        />
      ) : null}
    </div>
  );
}

/* ---------- 卡片分组 ---------- */

function ProviderGroup({
  title,
  providers,
  models,
  variant,
  onOpen,
  onManage,
}: {
  title: string;
  providers: ManageableProvider[];
  models: ManageableModel[];
  variant: "full" | "mini";
  onOpen: (provider: ManageableProvider) => void;
  onManage: (providerKey: string) => void;
}) {
  if (providers.length === 0) return null;
  return (
    <section className="skill-group">
      <h2 className="skill-group-title">{title}</h2>
      <div className="skill-card-grid">
        {providers.map((provider) => (
          <ProviderCard
            key={provider.provider_key}
            provider={provider}
            models={models}
            variant={variant}
            onOpen={() => onOpen(provider)}
            onManage={() => onManage(provider.provider_key)}
          />
        ))}
      </div>
    </section>
  );
}

function ProviderCard({
  provider,
  models,
  variant,
  onOpen,
  onManage,
}: {
  provider: ManageableProvider;
  models: ManageableModel[];
  variant: "full" | "mini";
  onOpen: () => void;
  onManage: () => void;
}) {
  const count = provider.enabled_models_count;
  const providerModels = models.filter((item) => item.provider_key === provider.provider_key);
  const enabledModels = providerModels.filter((item) => item.enabled);
  const modelNames = enabledModels.map((item) => item.display_name).join("、");
  const avatar = getProviderAvatar(provider.provider_key);
  return (
    <article className={variant === "mini" ? "skill-card provider-card-mini" : "skill-card"}>
      <div
        className="skill-card-content"
        role="button"
        tabIndex={0}
        aria-label={`配置供应商：${provider.display_name}`}
        onClick={onOpen}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen();
          }
        }}
      >
        <header className="skill-card-head provider-card-head">
          {avatar ? (
            <span
              className="provider-avatar has-logo"
              style={{ background: avatar.background }}
              aria-hidden
            >
              <img
                src={avatar.icon}
                alt=""
                loading="lazy"
                style={{
                  width: `${avatar.scale * 100}%`,
                  height: `${avatar.scale * 100}%`,
                  filter: avatar.filter,
                }}
              />
            </span>
          ) : (
            <span className="provider-avatar" aria-hidden>
              {provider.display_name.slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="skill-card-name" title={provider.display_name}>
            {provider.display_name}
          </span>
          {variant === "full" ? <span className="provider-dot is-on" /> : null}
        </header>
        <p className="skill-card-meta">
          {provider.provider_key}
        </p>
        {variant === "full" ? (
          <div className="provider-card-info">
            <div className="provider-card-row">
              <span className="provider-card-label">Base URL</span>
              <span className="provider-card-value" title={provider.base_url}>
                {provider.base_url}
              </span>
            </div>
            <div className="provider-card-row">
              <span className="provider-card-label">Model</span>
              <span className="provider-card-value" title={modelNames || undefined}>
                {modelNames || "暂无启用模型"}
              </span>
            </div>
          </div>
        ) : null}
      </div>
      {variant === "full" ? <footer className="skill-card-actions provider-card-actions">
        <Button
          className="provider-card-manage-button"
          size="small"
          type="text"
          icon={<Icon name="blocks" size={14} />}
          onClick={(event) => {
            event.stopPropagation();
            onManage();
          }}
        >
          管理模型（已启用 {count} 个）
        </Button>
      </footer> : null}
    </article>
  );
}

/* ---------- 供应商弹窗（仅管理员） ---------- */

function ProviderModal({
  providerKey,
  providers,
  userId,
  isAdmin,
  notify,
  modal,
  onClose,
  onChanged,
}: {
  providerKey: string | "new" | null;
  providers: ManageableProvider[];
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  modal: ReturnType<typeof AntdApp.useApp>["modal"];
  onClose: () => void;
  onChanged: () => void;
}) {
  const editing = providerKey !== null && providerKey !== "new";
  const provider = editing
    ? providers.find((item) => item.provider_key === providerKey) ?? null
    : null;
  const [form, setForm] = useState({
    providerKey: "",
    displayName: "",
    baseUrl: "",
    apiKey: "",
    apiKeyEnv: "",
    requestHeaders: "{}",
    extraConfig: "{}",
    modelsEndpoint: "",
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (provider) {
      setForm({
        providerKey: provider.provider_key,
        displayName: provider.display_name,
        baseUrl: provider.base_url,
        apiKey: "",
        apiKeyEnv: provider.api_key_env,
        requestHeaders: provider.has_request_headers ? "" : "{}",
        extraConfig: JSON.stringify(provider.extra_config, null, 2),
        modelsEndpoint: provider.models_endpoint ?? "",
        enabled: provider.enabled,
      });
    } else if (providerKey === "new") {
      setForm({
        providerKey: "",
        displayName: "",
        baseUrl: "",
        apiKey: "",
        apiKeyEnv: "",
        requestHeaders: "{}",
        extraConfig: "{}",
        modelsEndpoint: "",
        enabled: true,
      });
    }
  }, [provider, providerKey]);

  if (providerKey === null) return null;
  const isSystem = provider?.source_type === "system";

  const save = async () => {
    setSaving(true);
    try {
      const parseObject = (value: string, label: string): Record<string, unknown> => {
        let parsed: unknown;
        try { parsed = JSON.parse(value); } catch { throw new Error(`${label}必须是有效的 JSON 对象`); }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象`);
        return parsed as Record<string, unknown>;
      };
      const headers = form.requestHeaders.trim() ? parseObject(form.requestHeaders, "请求头") : undefined;
      if (headers && Object.values(headers).some((value) => typeof value !== "string")) throw new Error("请求头的值必须是字符串");
      const advanced = {
        apiKeyEnv: form.apiKeyEnv.trim(),
        requestHeaders: headers as Record<string, string> | undefined,
        extraConfig: parseObject(form.extraConfig, "扩展配置"),
      };
      if (providerKey === "new") {
        await createProvider({
          userId,
          ...advanced,
          providerKey: form.providerKey.trim(),
          scope: "global",
          displayName: form.displayName.trim(),
          baseUrl: form.baseUrl.trim(),
          apiKey: form.apiKey.trim() || null,
          modelsEndpoint: form.modelsEndpoint.trim() || null,
          enabled: form.enabled,
        });
        notify.success("已创建供应商");
      } else if (provider) {
        await updateProvider(provider.provider_key, {
          userId,
          ...advanced,
          enabled: form.enabled,
          displayName: form.displayName.trim() || undefined,
          baseUrl: form.baseUrl.trim() || undefined,
          apiKey: form.apiKey.trim() ? form.apiKey.trim() : undefined,
          modelsEndpoint: form.modelsEndpoint.trim(),
        });
        notify.success("已保存供应商");
      }
      onClose();
      onChanged();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const remove = () => {
    if (!provider) return;
    modal.confirm({
      title: "删除供应商？",
      content: `删除「${provider.display_name}」后无法恢复，需先删除其下模型。`,
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () =>
        deleteProvider(provider.provider_key, { userId })
          .then(() => {
            notify.success("已删除");
            onClose();
            onChanged();
          })
          .catch((error: unknown) =>
            notify.error(error instanceof Error ? error.message : String(error)),
          ),
    });
  };

  return (
    <Modal
      open
      className="provider-edit-modal"
      title={editing ? "编辑供应商" : "新增供应商"}
      onCancel={onClose}
      width={680}
      footer={
        <div className="provider-modal-footer">
          {editing && !isSystem ? (
            <Button danger disabled={saving} onClick={remove}>删除供应商</Button>
          ) : null}
          <span className="provider-modal-footer-spacer" />
          <Button disabled={saving} onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={() => void save()}>
            确定
          </Button>
        </div>
      }
    >
      <div className="provider-edit-form">
        <div className="provider-edit-grid">
          <label className="provider-edit-field">
            <span>Provider ID</span>
            <Input
              placeholder="如 siliconflow-cn"
              value={form.providerKey}
              disabled={editing || saving}
              onChange={(event) => setForm((current) => ({ ...current, providerKey: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>展示名称</span>
            <Input
              value={form.displayName}
              disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, displayName: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Base URL</span>
            <Input
              placeholder="https://api.example.com/v1"
              value={form.baseUrl}
              disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, baseUrl: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Provider Type</span>
            <Input value={provider?.provider_type === "openai_compatible" || !provider ? "OpenAI Completions API" : provider.provider_type} disabled />
          </label>
          <label className="provider-edit-field">
            <span>API Key Env</span>
            <Input value={form.apiKeyEnv} placeholder="如 DASHSCOPE_API_KEY" disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, apiKeyEnv: event.target.value }))} />
          </label>
          <label className="provider-edit-field">
            <span>API Key</span>
            <Input.Password
              autoComplete="new-password"
              name="provider-api-key"
              placeholder={editing ? "输入供应商API-Key(只保存不回显)" : "可留空稍后补"}
              value={form.apiKey}
              disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Models Endpoint</span>
            <Input
              placeholder="https://api.example.com/v1/models"
              value={form.modelsEndpoint}
              disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, modelsEndpoint: event.target.value }))}
            />
          </label>
        </div>
        <div className="provider-edit-status">
          <span id="provider-edit-status-label">状态</span>
          <Switch
            aria-labelledby="provider-edit-status-label"
            checkedChildren="启用"
            unCheckedChildren="停用"
            checked={form.enabled}
            disabled={saving}
            onChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))}
          />
        </div>
        <p className="resource-hint">停用并保存会清除共享 Key 和 API Key Env；个人 Key 保留。重新启用需重新配置共享凭据。</p>
        <details className="provider-edit-advanced">
          <summary>高级配置</summary>
          <label className="provider-edit-field">
            <span>请求头 JSON</span>
            <Input.TextArea rows={4} value={form.requestHeaders} disabled={saving}
              placeholder={provider?.has_request_headers ? "已配置（不回显）；留空保留，{} 清空" : "{}"}
              onChange={(event) => setForm((current) => ({ ...current, requestHeaders: event.target.value }))} />
          </label>
          <label className="provider-edit-field">
            <span>扩展配置 JSON</span>
            <Input.TextArea rows={4} value={form.extraConfig} disabled={saving}
              onChange={(event) => setForm((current) => ({ ...current, extraConfig: event.target.value }))} />
          </label>
          <p className="resource-hint">扩展配置作为附加请求体发送，请勿填写密钥。请求头留空保留，输入 {"{}"} 清空。</p>
        </details>
        <p className="resource-hint">
          {isAdmin ? "供应商配置全局共享" : "供应商由管理员统一管理"}
        </p>
      </div>
    </Modal>
  );
}

/* ---------- 供应商内模型管理弹窗 ---------- */

function ModelManageModal({
  providerKey,
  providers,
  models,
  userId,
  isAdmin,
  notify,
  onClose,
  onChanged,
}: {
  providerKey: string | null;
  providers: ManageableProvider[];
  models: ManageableModel[];
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  onClose: () => void;
  onChanged: () => void;
}) {
  const provider = providers.find((item) => item.provider_key === providerKey) ?? null;
  const canWrite = provider !== null && (isAdmin || provider.has_my_key);
  const modelScope = isAdmin ? "global" : "user";
  const enabledModels = models.filter(
    (item) => item.provider_key === providerKey && item.enabled,
  );
  const allModels = models.filter((item) => item.provider_key === providerKey);
  const [remote, setRemote] = useState<{ id: string; display_name: string }[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteSearch, setRemoteSearch] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [manual, setManual] = useState({ modelKey: "", displayName: "", modelName: "" });
  const [busyKey, setBusyKey] = useState("");

  useEffect(() => {
    setRemote([]);
    setRemoteSearch("");
    setShowManual(false);
  }, [providerKey]);

  if (providerKey === null) return null;

  const runAction = async (action: () => Promise<unknown>, successText: string) => {
    try {
      await action();
      if (successText) notify.success(successText);
      onChanged();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    }
  };

  const fetchRemote = async () => {
    if (!provider) return;
    setRemoteLoading(true);
    try {
      const result = await fetchRemoteModels(provider.provider_key, { userId });
      setRemote(result.items);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setRemoteLoading(false);
    }
  };

  const addRemote = async (item: { id: string; display_name: string }) => {
    if (!provider) return;
    const key = `${provider.provider_key}:${item.id}`;
    setBusyKey(key);
    try {
      const base = slugifyModelKey(item.id);
      let modelKey = base;
      let suffix = 2;
      while (models.some((row) => row.model_key === modelKey)) {
        modelKey = `${base}-${suffix}`;
        suffix += 1;
      }
      await createModel({
        userId,
        modelKey,
        providerKey: provider.provider_key,
        scope: modelScope,
        displayName: item.display_name || item.id,
        modelName: item.id,
      });
      notify.success(`已添加 ${item.id}`);
      onChanged();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyKey("");
    }
  };

  const submitManual = () => {
    if (!provider) return;
    void runAction(async () => {
      await createModel({
        userId,
        modelKey: manual.modelKey.trim(),
        providerKey: provider.provider_key,
        scope: modelScope,
        displayName: manual.displayName.trim(),
        modelName: manual.modelName.trim(),
      });
      setShowManual(false);
      setManual({ modelKey: "", displayName: "", modelName: "" });
    }, "已创建模型");
  };

  const remoteAddedIds = new Set(allModels.filter((item) => item.scope === modelScope).map((item) => item.model_name));
  const remoteFiltered = remote.filter((item) =>
    item.id.toLowerCase().includes(remoteSearch.trim().toLowerCase()),
  );

  return (
    <Modal
      open={providerKey !== null}
      className="model-manage-modal"
      title={provider ? `管理模型 · ${provider.display_name}` : "管理模型"}
      footer={null}
      onCancel={onClose}
      width={800}
    >
      <h3 className="skill-group-title">已启用模型（{enabledModels.length}）</h3>
      <ul className="resource-list">
        {allModels.map((model) => {
          return (
            <li key={model.model_key} className="resource-item">
              <span className="resource-copy">
                <span className="resource-title">
                  {model.display_name}
                  {model.is_default ? <span className="resource-badge">默认</span> : null}
                  {!model.enabled ? <span className="resource-warning">（已停用）</span> : null}
                </span>
                <span className="resource-description">
                  {model.model_key} · {model.model_name}
                </span>
              </span>
              {(model.scope === "global" ? isAdmin : model.created_by === userId) ? (
                <>
                  <Switch
                    size="small"
                    checked={model.enabled}
                    onChange={(checked) =>
                      void runAction(
                        () => updateModel(model.model_key, { userId, enabled: checked }),
                        checked ? "已启用" : "已停用",
                      )
                    }
                  />
                  {!model.is_default && model.enabled ? (
                    <Button
                      size="small"
                      onClick={() =>
                        void runAction(
                          () => updateModel(model.model_key, { userId, isDefault: true }),
                          "已设为默认模型",
                        )
                      }
                    >
                      设为默认
                    </Button>
                  ) : null}
                  <Button
                    size="small"
                    danger
                    onClick={() =>
                      void runAction(() => deleteModel(model.model_key, { userId }), "已删除")
                    }
                  >
                    删除
                  </Button>
                </>
              ) : null}
            </li>
          );
        })}
        {allModels.length === 0 ? (
          <li className="resource-empty">该供应商下还没有模型，先拉取远端或手动添加。</li>
        ) : null}
      </ul>

      {canWrite ? (
        <div className="resource-actions" style={{ marginTop: 12 }}>
          <Button
            icon={<Icon name="refresh-cw" size={14} />}
            loading={remoteLoading}
            disabled={!provider?.models_endpoint}
            onClick={() => void fetchRemote()}
          >
            获取远程模型
          </Button>
          <Button icon={<Icon name="plus" size={14} />} onClick={() => setShowManual((v) => !v)}>
            手动添加
          </Button>
          {!provider?.models_endpoint ? (
            <span className="resource-hint">该供应商未配置模型列表端点</span>
          ) : null}
        </div>
      ) : (
        <p className="resource-hint" style={{ marginTop: 12 }}>
          请先配置自己的 API Key，再添加个人模型。管理员共享的模型可直接使用。
        </p>
      )}

      {showManual && canWrite ? (
        <div className="resource-form" style={{ marginTop: 8 }}>
          <div className="resource-form-row">
            <Input
              placeholder="模型标识（如 glm-5-2，小写字母/数字/_/-）"
              value={manual.modelKey}
              onChange={(event) => setManual((c) => ({ ...c, modelKey: event.target.value }))}
            />
            <Input
              placeholder="显示名称（如 GLM 5.2）"
              value={manual.displayName}
              onChange={(event) => setManual((c) => ({ ...c, displayName: event.target.value }))}
            />
          </div>
          <Input
            placeholder="远端模型名（如 zai-org/GLM-5.2）"
            value={manual.modelName}
            onChange={(event) => setManual((c) => ({ ...c, modelName: event.target.value }))}
          />
          <Button type="primary" onClick={submitManual}>
            创建
          </Button>
        </div>
      ) : null}

      {remote.length > 0 ? (
        <div style={{ marginTop: 12 }}>
          <div className="resource-actions model-remote-toolbar">
            <Input
              allowClear
              placeholder="搜索远端模型..."
              prefix={<Icon name="search" size={14} />}
              value={remoteSearch}
              onChange={(event) => setRemoteSearch(event.target.value)}
              className="skill-search"
            />
            <span className="resource-hint">远端候选（{remoteFiltered.length}）</span>
          </div>
          <ul className="resource-list" style={{ marginTop: 8 }}>
            {remoteFiltered.slice(0, 100).map((item) => {
              const added = remoteAddedIds.has(item.id);
              const busy = busyKey === `${provider?.provider_key}:${item.id}`;
              return (
                <li key={item.id} className="resource-item">
                  <span className="resource-copy">
                    <span className="resource-title">{item.display_name}</span>
                    <span className="resource-description">{item.id}</span>
                  </span>
                  {canWrite ? (
                    added ? (
                      <span className="resource-hint">已添加</span>
                    ) : (
                      <Button size="small" loading={busy} onClick={() => void addRemote(item)}>
                        添加
                      </Button>
                    )
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </Modal>
  );
}

/* ---------- 我的 Key 弹窗（所有用户） ---------- */

function MyKeyModal({
  provider,
  userId,
  notify,
  onClose,
  onChanged,
}: {
  provider: ManageableProvider;
  userId: string;
  notify: Notify;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await setMyProviderKey(provider.provider_key, { userId, apiKey: apiKey.trim() });
      notify.success("已保存我的 Key");
      onClose();
      onChanged();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    try {
      await deleteMyProviderKey(provider.provider_key, { userId });
      notify.success("已清除我的 Key，个人模型将不可用");
      onClose();
      onChanged();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Modal
      open={provider !== null}
      className="provider-key-modal"
      title={`我的 Key · ${provider?.display_name}`}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onClose}
      onOk={() => void save()}
      width={520}
    >
      <div className="resource-form">
        <p className="resource-hint provider-key-url">{provider.base_url}</p>
        <Input.Password
          autoComplete="new-password"
          name={`provider-api-key-${provider.provider_key}`}
          placeholder="输入供应商API-Key(只保存不回显)"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
        />
        {provider?.has_my_key ? (
          <Button className="provider-key-clear" danger onClick={() => void clear()}>
            清除我的 Key
          </Button>
        ) : null}
      </div>
    </Modal>
  );
}
