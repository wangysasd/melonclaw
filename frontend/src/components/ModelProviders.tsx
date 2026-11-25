import { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Checkbox, Dropdown, Input, Modal, Select, Switch } from "antd";

import {
  apiRequest,
  createModel,
  createProvider,
  deleteModel,
  deleteMyProviderKey,
  deleteProvider,
  fetchRemoteModels,
  listManageableModels,
  listManageableProviders,
  setMyProviderKey,
  testProviderConnection,
  updateModel,
  updateProvider,
} from "../api/client";
import type { ManageableModel, ManageableProvider } from "../types/api";
import { ChatModelSettingsEditor, ContextWindowInput, DEFAULT_CONTEXT_WINDOW } from "./ModelContextWindow";
import { Icon } from "./Icon";
import { deleteConfirmation } from "./deleteConfirmation";
import { getProviderAvatar } from "./providerIcons";

interface Notify {
  success: (content: string) => void;
  error: (content: string) => void;
}

type ModelPurpose = "chat" | "document_parser";

const PROVIDER_TYPE_OPTIONS = [
  { value: "openai_compatible", label: "OpenAI Completions API" },
  { value: "anthropic", label: "Anthropic Messages API" },
];

interface ModelSectionProps {
  purpose?: ModelPurpose;
  userId: string;
  isAdmin: boolean;
  notify: Notify;
  onChanged?: () => void;
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label}必须是有效的 JSON 对象`); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象`);
  return parsed as Record<string, unknown>;
}

function parseProviderHeaders(value: string): Record<string, string> | undefined {
  if (!value.trim()) return undefined;
  const headers = parseJsonObject(value, "请求头");
  if (Object.values(headers).some((item) => typeof item !== "string")) {
    throw new Error("请求头的值必须是字符串");
  }
  return headers as Record<string, string>;
}

/**
 * 模型供应商区：先配供应商，再进供应商配模型。
 * - 顶栏：搜索 + 新增供应商（仅 admin）+ 刷新；
 * - 分组：已启用大卡 / 未启用小卡；
 * - 点击卡片：admin 进供应商编辑弹窗，普通用户进“我的 Key”弹窗；
 * - 卡片 footer「管理模型」进模型管理弹窗（含远端拉取一键启用）。
 */
export function ModelSection({ userId, isAdmin, notify, onChanged, purpose = "chat" }: ModelSectionProps) {
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
        listManageableModels({ userId, purpose }),
      ]);
      setProviders(providerData.items);
      setModels(modelData.items);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [userId, notify, purpose]);

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
          <Button className="action-secondary"
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
          : "管理员共享的模型可直接使用；已启用仅表示你已配置自己的 API Key"}。目前仅支持 OpenAI API 兼容协议。
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
        key={purpose}
        purpose={purpose}
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
      <div className={`skill-card-grid provider-card-grid provider-card-grid-${variant}`}>
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
    providerType: "openai_compatible",
    baseUrl: "",
    apiKey: "",
    apiKeyEnv: "",
    requestHeaders: "{}",
    extraConfig: "{}",
    modelsEndpoint: "",
    enabled: true,
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (provider) {
      setForm({
        providerKey: provider.provider_key,
        displayName: provider.display_name,
        providerType: provider.provider_type,
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
        providerType: "openai_compatible",
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
      const headers = parseProviderHeaders(form.requestHeaders);
      const anthropic = form.providerType === "anthropic";
      const advanced = {
        apiKeyEnv: form.apiKeyEnv.trim(),
        requestHeaders: headers,
        extraConfig: anthropic ? {} : parseJsonObject(form.extraConfig, "扩展配置"),
      };
      if (providerKey === "new") {
        await createProvider({
          userId,
          ...advanced,
          providerKey: form.providerKey.trim(),
          scope: "global",
          displayName: form.displayName.trim(),
          providerType: form.providerType,
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
          providerType: form.providerType === "anthropic" ? "anthropic" : "openai_compatible",
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

  const testConnection = async () => {
    let requestHeaders: Record<string, string> | undefined;
    try {
      requestHeaders = parseProviderHeaders(form.requestHeaders);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
      return;
    }
    setTesting(true);
    try {
      const result = await testProviderConnection({
        userId,
        providerKey: editing ? provider?.provider_key : undefined,
        providerType: form.providerType === "anthropic" ? "anthropic" : "openai_compatible",
        baseUrl: form.baseUrl.trim(),
        modelsEndpoint: form.modelsEndpoint.trim(),
        apiKey: form.apiKey.trim() || undefined,
        apiKeyEnv: form.apiKeyEnv.trim(),
        requestHeaders,
      });
      notify.success(`连接成功，可获取 ${result.model_count} 个模型。`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : String(error));
    } finally {
      setTesting(false);
    }
  };

  const remove = () => {
    if (!provider) return;
    modal.confirm({
      ...deleteConfirmation({
        title: "删除供应商？", name: provider.display_name,
        metadata: `供应商 ID：${provider.provider_key}`, icon: <Icon name="brain" size={24} />,
        description: "删除后无法恢复，需先删除其下模型。",
      }),
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
      onCancel={() => { if (!saving && !testing) onClose(); }}
      closable={!saving && !testing}
      width={680}
      footer={
        <div className="provider-modal-footer">
          <Button
            className="action-secondary"
            disabled={saving}
            loading={testing}
            onClick={() => void testConnection()}
          >
            测试连接
          </Button>
          {editing && !isSystem ? (
            <Button danger disabled={saving || testing} onClick={remove}>删除供应商</Button>
          ) : null}
          <span className="provider-modal-footer-spacer" />
          <Button className="action-secondary" disabled={saving || testing} onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} disabled={testing} onClick={() => void save()}>
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
              disabled={editing || saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, providerKey: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>展示名称</span>
            <Input
              value={form.displayName}
              disabled={saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, displayName: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Base URL</span>
            <Input
              placeholder="https://api.example.com/v1"
              value={form.baseUrl}
              disabled={saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, baseUrl: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Provider Type</span>
            <Select
              aria-label="Provider Type"
              value={form.providerType}
              disabled={saving || testing}
              options={PROVIDER_TYPE_OPTIONS}
              onChange={(value: string) => setForm((current) => ({ ...current, providerType: value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>API Key Env</span>
            <Input value={form.apiKeyEnv} placeholder="如 DASHSCOPE_API_KEY" disabled={saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, apiKeyEnv: event.target.value }))} />
          </label>
          <label className="provider-edit-field">
            <span>API Key</span>
            <Input.Password
              autoComplete="new-password"
              name="provider-api-key"
              placeholder={editing ? "输入供应商API_Key(只保存不回显)" : "可留空稍后补"}
              value={form.apiKey}
              disabled={saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))}
            />
          </label>
          <label className="provider-edit-field">
            <span>Models Endpoint</span>
            <Input
              placeholder="https://api.example.com/v1/models"
              value={form.modelsEndpoint}
              disabled={saving || testing}
              onChange={(event) => setForm((current) => ({ ...current, modelsEndpoint: event.target.value }))}
            />
          </label>
        </div>
        {form.providerType === "anthropic" ? (
          <p className="resource-hint">Anthropic Base URL 使用服务根地址或网关前缀，客户端会追加 /v1/messages；模型列表端点留空时使用 /v1/models。</p>
        ) : null}
        <div className="provider-edit-status">
          <span id="provider-edit-status-label">状态</span>
          <Switch
            aria-labelledby="provider-edit-status-label"
            checkedChildren="启用"
            unCheckedChildren="停用"
            checked={form.enabled}
            disabled={saving || testing}
            onChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))}
          />
        </div>
        <p className="resource-hint">停用并保存会清除共享 Key 和 API Key Env；个人 Key 保留。重新启用需重新配置共享凭据。</p>
        <details className="provider-edit-advanced">
          <summary>高级配置</summary>
          <label className="provider-edit-field">
            <span>请求头 JSON</span>
            <Input.TextArea rows={4} value={form.requestHeaders} disabled={saving || testing}
              placeholder={provider?.has_request_headers ? "已配置（不回显）；留空保留，{} 清空" : "{}"}
              onChange={(event) => setForm((current) => ({ ...current, requestHeaders: event.target.value }))} />
          </label>
          {form.providerType === "anthropic" ? null : (
          <>
          <label className="provider-edit-field">
            <span>思考输出格式</span>
            <Select
              aria-label="思考输出格式"
              disabled={saving || testing}
              value={(() => {
                try { return JSON.parse(form.extraConfig)._melonclaw?.reasoning_format ?? "openai"; }
                catch { return "openai"; }
              })()}
              options={[
                { value: "openai", label: "标准 OpenAI（默认）" },
                { value: "reasoning_content", label: "独立思考字段（DeepSeek）" },
                { value: "think_tags", label: "正文 think 标签（MiniMax）" },
                { value: "reasoning_details", label: "思考详情字段（OpenRouter）" },
              ]}
              onChange={(value: string) => {
                try {
                  const config = parseJsonObject(form.extraConfig, "扩展配置");
                  config._melonclaw = { reasoning_format: value };
                  setForm((current) => ({ ...current, extraConfig: JSON.stringify(config, null, 2) }));
                } catch (error) {
                  notify.error(error instanceof Error ? error.message : String(error));
                }
              }}
            />
          </label>
          <label className="provider-edit-field">
            <span>扩展配置 JSON</span>
            <Input.TextArea rows={4} value={form.extraConfig} disabled={saving || testing}
              aria-describedby="provider-extra-config-help"
              onChange={(event) => setForm((current) => ({ ...current, extraConfig: event.target.value }))} />
          </label>
          <p className="resource-hint provider-edit-help" id="provider-extra-config-help">
            上方选择会同步到 <code>_melonclaw.reasoning_format</code>。
            <code>_melonclaw</code> 仅用于本地解析，其余扩展配置作为附加请求体发送，请勿填写密钥。
          </p>
          </>
          )}
          <p className="resource-hint">请求头留空保留，输入 {"{}"} 清空。</p>
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
  purpose,
  providerKey,
  providers,
  models,
  userId,
  isAdmin,
  notify,
  onClose,
  onChanged,
}: {
  purpose: ModelPurpose;
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
  const canWrite = provider !== null && (isAdmin || (purpose === "chat" && provider.has_my_key));
  const modelScope = isAdmin ? "global" : "user";
  const enabledModels = models.filter(
    (item) => item.provider_key === providerKey && item.enabled,
  );
  const allModels = models.filter((item) => item.provider_key === providerKey);
  const [remote, setRemote] = useState<{ id: string; display_name: string }[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteSearch, setRemoteSearch] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [manual, setManual] = useState({ displayName: "", modelName: "", supportsImageInput: true, contextWindow: DEFAULT_CONTEXT_WINDOW as number | null });
  const { modal } = AntdApp.useApp();
  const [editingModel, setEditingModel] = useState<ManageableModel | null>(null);
  const [editName, setEditName] = useState("");
  const [editModelName, setEditModelName] = useState("");
  const [editingChatModelKey, setEditingChatModelKey] = useState<string | null>(null);
  const [remoteWithoutImageInput, setRemoteWithoutImageInput] = useState<Set<string>>(() => new Set());
  const [busyKey, setBusyKey] = useState("");

  useEffect(() => {
    setEditingModel(null);
    setEditingChatModelKey(null);
    setRemoteWithoutImageInput(new Set());
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
      const result = await fetchRemoteModels(provider.provider_key, { userId, purpose });
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
      await createModel({
        userId,
        providerKey: provider.provider_key,
        scope: modelScope,
        displayName: item.display_name || item.id,
        modelName: item.id,
        purpose,
        contextWindow: purpose !== "document_parser" ? DEFAULT_CONTEXT_WINDOW : null,
        supportsImageInput: purpose === "chat" ? !remoteWithoutImageInput.has(item.id) : undefined,
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
    const contextWindow = manual.contextWindow;
    if (!provider || (purpose !== "document_parser" && (!contextWindow || contextWindow <= 0))) return;
    void runAction(async () => {
      await createModel({
        userId,
        providerKey: provider.provider_key,
        scope: modelScope,
        displayName: manual.displayName.trim(),
        modelName: manual.modelName.trim(),
        purpose,
        contextWindow: purpose !== "document_parser" ? contextWindow! : null,
        supportsImageInput: purpose === "chat" ? manual.supportsImageInput : undefined,
      });
      setShowManual(false);
      setManual({ displayName: "", modelName: "", supportsImageInput: true, contextWindow: DEFAULT_CONTEXT_WINDOW as number | null });
    }, "已创建模型");
  };

  const hasRemoteEndpoint = Boolean(provider?.models_endpoint || provider?.provider_type === "anthropic");
  const createManualButton = (
    <Button className="manual-model-create" type="primary" onClick={submitManual} disabled={!manual.displayName.trim() || !manual.modelName.trim() || (purpose === "chat" && (!manual.contextWindow || manual.contextWindow <= 0))}>
      创建
    </Button>
  );
  const remoteAddedIds = new Set(allModels.filter((item) => item.scope === modelScope).map((item) => item.model_name));
  const remoteFiltered = remote.filter((item) =>
    item.id.toLowerCase().includes(remoteSearch.trim().toLowerCase()),
  );

  return (
    <>
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
          const canEdit = model.scope === "global" ? isAdmin : model.created_by === userId;
          const editing = editingChatModelKey === model.model_key;
          const supportsImageInput = model.input_modalities.includes("image");
          const setDefault = () => void runAction(
            () => updateModel(model.model_key, { userId, isDefault: true }),
            "已设为默认模型",
          );
          const confirmDelete = () => modal.confirm({
            ...deleteConfirmation({ title: "删除模型？", name: model.display_name, metadata: model.model_name, icon: <Icon name="brain" size={20} />, description: "已绑定知识库的模型不能删除。" }),
            onOk: async () => { await deleteModel(model.model_key, { userId }); onChanged(); },
          });
          const toggle = (
            <Switch size="small" checked={model.enabled} aria-label={`启用 ${model.display_name}`}
              onChange={(checked) => void runAction(
                () => updateModel(model.model_key, { userId, enabled: checked }),
                checked ? "已启用" : "已停用",
              )}
            />
          );
          return (
            <li key={model.model_key} className={purpose === "chat" ? "resource-item chat-model-row" : "resource-item"}>
              <span className="resource-copy">
                <span className="resource-title">
                  {model.display_name}
                  {model.is_default ? <span className="resource-badge model-default-badge">默认</span> : null}
                  {!model.enabled ? <span className="resource-warning">（已停用）</span> : null}
                </span>
                <span className="resource-description">
                  {model.model_name}
                  {purpose === "document_parser" ? " · 文档解析" : null}
                </span>
                {purpose === "chat" ? (
                  <span className="model-capabilities">
                    <span className={supportsImageInput ? "resource-badge model-image-badge" : "resource-badge"}>
                      {supportsImageInput ? "支持图片输入" : "不支持图片输入"}
                    </span>
                    <span className="resource-badge">
                      上下文 {model.context_window == null ? "未设置" : `${model.context_window.toLocaleString()} tokens`}
                    </span>
                  </span>
                ) : null}
              </span>
              {canEdit && (purpose === "chat" ? (
                <div className="chat-model-actions">
                  <Button size="small" aria-expanded={editing} onClick={() => setEditingChatModelKey(editing ? null : model.model_key)}>
                    编辑
                  </Button>
                  {toggle}
                  <Dropdown trigger={["click"]} menu={{ items: [
                    { key: "default", label: "设为默认", disabled: model.is_default || !model.enabled, onClick: setDefault },
                    { key: "delete", label: "删除模型", danger: true, onClick: confirmDelete },
                  ] }}>
                    <Button size="small" aria-label={`${model.display_name}的更多操作`} aria-haspopup="menu" title="更多操作">…</Button>
                  </Dropdown>
                </div>
              ) : (
                <>
                  <Button size="small" onClick={() => { setEditingModel(model); setEditName(model.display_name); setEditModelName(model.model_name); }}>编辑</Button>
                  <Button size="small" loading={busyKey === model.model_key} onClick={() => {
                    setBusyKey(model.model_key);
                    void runAction(() => apiRequest<{ summary: string }>(`/api/models/${encodeURIComponent(model.model_key)}/test-connection`, { method: "POST" }).then((r) => notify.success(r.summary)), "").finally(() => setBusyKey(""));
                  }}>连接测试</Button>
                  {toggle}
                  {!model.is_default && model.enabled ? <Button type="primary" size="small" onClick={setDefault}>设为默认</Button> : null}
                  <Button size="small" danger onClick={confirmDelete}>删除</Button>
                </>
              ))}
              {canEdit && purpose === "chat" && editing ? (
                <ChatModelSettingsEditor
                  model={model}
                  userId={userId}
                  onCancel={() => setEditingChatModelKey(null)}
                  onSaved={() => {
                    setEditingChatModelKey((current) => current === model.model_key ? null : current);
                    onChanged();
                  }}
                />
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
          <Button className="action-secondary"
            icon={<Icon name="refresh-cw" size={14} />}
            loading={remoteLoading}
            disabled={!hasRemoteEndpoint}
            onClick={() => void fetchRemote()}
          >
            获取远程模型
          </Button>
          <Button icon={<Icon name="plus" size={14} />} onClick={() => setShowManual((v) => !v)}>
            手动添加
          </Button>
          {!hasRemoteEndpoint ? (
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
          <div className="manual-model-fields">
            <label className="provider-field">
              模型名称
              <Input
                placeholder="用于显示的名称，如 DeepSeek-V4.1-Flash"
                value={manual.displayName}
                onChange={(event) => setManual((c) => ({ ...c, displayName: event.target.value }))}
              />
            </label>
            <label className="provider-field">
              模型 ID
              <Input
                placeholder="供应商 API 请求中 model 的值，如 deepseek-flash"
                value={manual.modelName}
                onChange={(event) => setManual((c) => ({ ...c, modelName: event.target.value }))}
              />
            </label>
            {purpose === "chat" ? (
              <>
                <ContextWindowInput value={manual.contextWindow} onChange={(value) => {
                  setManual((current) => ({ ...current, contextWindow: value }));
                }} />
                <div className="manual-model-actions">
                  <Checkbox checked={manual.supportsImageInput} onChange={(event) => {
                    const supportsImageInput = event.target.checked;
                    setManual((current) => ({ ...current, supportsImageInput }));
                  }}>支持图片输入</Checkbox>
                  {createManualButton}
                </div>
              </>
            ) : null}
          </div>
          {purpose === "document_parser" ? (
            <>
              <p className="resource-hint">使用 OpenAI 兼容图片识别接口；不设置聊天上下文窗口</p>
              {createManualButton}
            </>
          ) : null}
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
                      <>
                        {purpose === "chat" ? (
                          <Checkbox checked={!remoteWithoutImageInput.has(item.id)} disabled={busy} onChange={(event) => {
                            const checked = event.target.checked;
                            setRemoteWithoutImageInput((current) => {
                              const next = new Set(current);
                              if (checked) next.delete(item.id);
                              else next.add(item.id);
                              return next;
                            });
                          }}>支持图片输入</Checkbox>
                        ) : null}
                        <Button type="primary" size="small" loading={busy} onClick={() => void addRemote(item)}>
                          添加
                        </Button>
                      </>
                    )
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </Modal>
    <Modal
      title="编辑知识库模型"
      open={editingModel !== null}
      onCancel={() => setEditingModel(null)}
      onOk={() => void runAction(async () => {
        if (!editingModel) return;
        await updateModel(editingModel.model_key, { userId, displayName: editName, modelName: editModelName });
        setEditingModel(null);
      }, "已更新模型")}
    >
      <label>显示名称<Input value={editName} onChange={(e) => setEditName(e.target.value)} /></label>
      <label>模型名称<Input value={editModelName} onChange={(e) => setEditModelName(e.target.value)} /></label>
      <p className="resource-hint">已绑定的模型语义不能修改。供应商 Key 在既有供应商编辑中维护。</p>
    </Modal>
    </>
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
          placeholder="输入供应商API_Key(只保存不回显)"
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
