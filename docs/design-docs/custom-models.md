# 模型目录数据库化（原"用户自定义模型"设计已合并升级）

- 状态：已实现（2026-09-26 Yuxi 对齐：23 家纯 chat 供应商、两级 scope、用户 Key 覆盖、卡片+弹窗交互）
- 背景与目标：模型与凭据由用户登录后配置，管理员配置全局内置模型，普通用户配置个人模型。初始化不创建默认模型，运行时不提供环境变量模型兜底。
- 参考：Yuxi 的供应商卡片分组与两层弹窗交互（`ModelProviderManagePanel.vue`），但保留 MelonClaw 供应商、模型与个人 Key 三表结构（不学 Yuxi 单表 JSON）；权限沿用两级 scope：admin 配全局共享（默认给全员），用户配私有，各人启用的集合不一样。

## 方案概览

### 数据模型

- `model_providers` 表：`provider_key`（全局唯一）、scope（数据库及管理接口仅允许 `global`，管理员维护）、source_type（`system`=23 家种子 / `manual`=新建）、display_name、provider_type（目前只有 `openai_compatible`）、base_url、api_key（共享 Key，只写不回读）、models_endpoint、enabled、created_by、version。
- `model_configs` 表：`model_key`（全局唯一，对外模型 ID 统一为 `custom:<model_key>`）、`provider_key`（FK → 供应商，RESTRICT）、scope（`global`=管理员共享 / `user`=用户个人）、source_type（`system`=种子 / `manual`=新建）、display_name、model_name（远端真实名）、enabled、is_default（部分唯一索引全局至多一行、每个用户至多一行）、input_modalities、created_by、version。
- `provider_user_keys` 表：`(provider_key,user_id)` 唯一，用户在共享供应商上的 Key 覆盖；全局模型运行时优先级 用户Key > 共享Key > 指定环境变量，个人模型仅使用个人 Key；供应商删除时级联清理。
- `version` 列每次更新自增，进 `ResolvedModel.config_version/provider_version` 与 Agent 缓存键：密钥轮换、改 Base URL 等变更下一条消息即生效。
- 所有模型走 `openai_compatible` 适配器，纯 chat；不含 embedding/rerank 能力与多 base_url。
- 初始化 23 家供应商模板，全部停用、无 Key、无环境变量引用；不写 model_configs。供应商模板按 `INSERT ... ON CONFLICT DO NOTHING` 补录，不覆盖配置。

### 解析链路

- `GET /api/models` 返回当前用户可见的模型行（global + 自己的 user scope）转换的目录；未配置 api_key 的模型 `available=false`，不进选择器可用项。没有可用模型时 default_model_id 为空，不补造目录条目。
- 请求/回放统一走 `ChatRuntime.resolve_model(user_id, model_id)`：`custom:` 前缀查 `model_configs`，行不存在、不可见或已停用时报错；其他 ID 直接报错。
- 默认模型解析（`model_id=None`）：可用个人默认 → 可用全局默认 → 首个可用内置模型 → 首个可用个人模型 → 提示先配置模型。`is_default` 由管理端 `PATCH /api/models/{model_key}` 切换（单事务清旧默认置新默认）。
- 历史消息回放（`model_for_message`）遇到已被删除/停用的模型时回落到当前默认模型，避免旧会话因配置消失而无法打开。

### 管理 API 与权限矩阵

- `GET /api/models/manage`（可管理全集）、`POST /api/models`、`PATCH/DELETE /api/models/{model_key}`，与 MCP 管理路由同风格；供应商一组：`GET/POST /api/model-providers`、`PATCH/DELETE /api/model-providers/{key}`、`GET .../remote-models`、`PUT/DELETE .../my-key`。
- 供应商仅 admin/owner 可管理，创建时必须为 global；普通用户不能创建供应商。模型仍分 global（管理员管理，全员可用）和 user（本人管理）。普通用户通过既有供应商填写个人 API Key、管理个人模型；「已启用」分组仅反映自己的 Key 是否配置，与共享模型可用性分开。
- **api_key 只写不回读**：所有响应只带 `has_api_key/has_my_key/effective_has_key` 布尔位；更新时 `api_key` 缺省表示保留已存密钥；用户 Key 清除走删除接口。
- **删除权限**：内置供应商模板不可删除；模型不受 source_type 限制，管理员可删除全局模型，用户可删除自己的个人模型。

### 前端

- 资源管理「模型」Tab（`ModelProviders.tsx`，学 Yuxi）：搜索 + 已启用/未启用分组卡片（大卡含 Base URL、启用模型名称与模型数；名称在 Base URL 下方以 12px 单行显示，超长省略且悬停可查看完整列表，绿点表启用，logo 见 `providerIcons.ts`）+ 新增供应商 + 刷新；只有管理员可新增供应商，普通用户按个人 Key 配置状态分组；点击卡片：有写权限进供应商弹窗，否则进“我的 Key”弹窗；卡片 footer「管理模型」进供应商内模型弹窗。
- 供应商弹窗：标识（编辑禁用）、显示名、Base URL、API Key（编辑留空保持不变）、模型列表端点、启用开关、删除（非 system）。
- 模型管理弹窗：已启用模型表（启停/移除；管理员设全局默认，用户设个人默认）+ 获取远程模型 + 手动添加；远端候选支持搜索，一键启用自动 slugify 生成 `model_key` 并去重；无写权限时只读。
- 发送按钮旁的模型选择器自动包含可见模型，可用性按有效 Key（我的优先）判定，无需额外接线。

## 关键取舍

- **不做任意 provider 适配器**：所有模型统一 OpenAI 兼容接口（覆盖 DeepSeek/MiniMax/OpenAI 及绝大多数自建网关），目录行的 provider 字段只是展示标签，唯一的分派键是 `adapter_type`。出现非 OpenAI 兼容需求时再按 adapter_type 扩展。
- **不做模型共享给指定用户/租户**：供应商固定为 `global`，模型仅允许 `global/user`，不预留 `tenant`。
- **不做历史 ID 兼容**：旧 `system:xxx` 模型 ID 随本次升级废弃（历史数据可清空），统一使用 `custom:<model_key>`。
- **目录来源收敛到服务端**（`runtime.models(user_id)`），前端选择器零改动拿到完整列表。
- **供应商模板与模型分离**：保留 OpenAI 等供应商模板，但不预置任何模型或凭据。

## 失败与安全边界

- 模型解析失败（不存在/不可见/停用/缺 Key）统一报"所选模型不存在或当前不可用"或"所选模型未配置 API Key"，不回显细节。
- 凭据硬规则同 MCP：模型连接信息与环境变量名显式存储在 DB 行内；只通过管理员配置的 `api_key_env` 读取共享凭据，不展开任意 `${VAR}` 占位符。
- 全局 `models_revision`（`max(updated_at)`）进 Agent 缓存键：任何用户改模型配置会让所有缓存键变一次，多重建一个 Agent，换取实现简单与多进程一致（与 Skill/MCP 相同取舍）。
- **无隐式模型**：Settings 不携带模型连接参数，Agent 必须接收数据库解析后的 ResolvedModel；无模型不影响服务启动。

## 验证方式

- `tests/test_model_configs.py`：载荷校验、api_key 不泄漏断言、解析与 cache_key、完整权限矩阵、种子行删除保护、默认模型切换权限。
- `tests/test_bootstrap.py`：供应商模板无凭据、全部停用，忽略模型环境变量。
- `tests/test_runtime_models.py`：空目录、无 Key 不可用、无默认时提示配置、权限与个人 Key 隔离。
- `tests/test_schema.py`：表结构约束（model_key 唯一、source_type CHECK、is_default 部分唯一索引、created_by 外键）。
- 端到端：初始化后目录为空 → 登录配置 Key 和模型 → 管理员模型全员可见，个人模型仅本人可见。


## 供应商编辑与高级配置（2026-09-27）

点击供应商卡片后，管理员进入「编辑供应商」双列表单：Provider ID 编辑时锁定，Provider Type 当前只支持 OpenAI Completions API；名称、Base URL、API Key Env、API Key、Models Endpoint 可修改。状态独占一行，「高级配置」默认收起，点击后展开。「确定」保存表单中的启用状态；内置供应商不能删除，手动创建的供应商仍需先移除模型才能删除。普通用户沿用个人 Key 弹窗，无权修改共享供应商。

高级配置存放于 `model_providers.api_key_env / request_headers / extra_config`：

- API Key Env 仅接受大写、以 `_API_KEY` 或 `_ACCESS_TOKEN` 结尾的变量名。全局模型凭据优先级：个人 Key → 数据库共享 Key → 指定环境变量；个人模型只使用个人 Key。初始化不读取环境变量或复制凭据。修改环境变量后重启服务。

- 请求头 JSON 是字符串键值对象，聊天通过 `ChatOpenAI.default_headers` 注入，远端模型列表请求也携带这些头。保存后只返回 `has_request_headers`，绝不回显值；编辑时留空保留，`{}` 清空。禁止覆盖 Authorization、Host、Cookie、Content-Type 等认证/传输控制头，拒绝 CR/LF、非 ASCII 和重复头名。
- 扩展配置 JSON 是供应商附加请求体，通过 `ChatOpenAI.extra_body` 注入，不是任意 SDK 构造参数。支持供应商自定义参数，如 `{"enable_thinking": true}`。不用于模型列表 GET；禁止覆盖模型、消息、工具和流式协议，也拒绝嵌套凭据字段、非有限数和超过 16 KiB 的对象。该配置可回显，请勿写入秘密。

管理员保存走现有 API 权限检查；配置更新递增供应商版本，后续运行重建模型实例。没有新增 Agent 工具或 HITL 条目，既有文件和 Shell 权限不变；开发模拟身份仍非生产认证。请求失败不回显请求头或连接异常详情。

表结构按开发期约定重建，不增加迁移或历史字段兜底。删除并重建模型相关三表（仅清空行无效）后执行 `uv run melonclaw-db-init`，再执行 `scripts/restart.sh`。重建会删除自定义模型和个人 Key，仅恢复无凭据供应商模板；与会话、Project、附件表无级联删除关系。

验证：`scripts/check.sh` 覆盖前后端检查，新增用例验证表单保存/非法 JSON、凭据优先级、头部不回显、模型构造参数与协议保护。真实供应商调用需有效 Key 和远端服务，本次不发送付费模型请求。

请求头同时从模型 repr 隐藏，并通过 `lc_secrets` 在 LangChain 序列化中脱敏，防止进入模型追踪元信息。实际 API 创建/更新/回读/清空/启用验证通过。附加请求体语义见 [ChatOpenAI 官方参考](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI)。

供应商信息区的 Base URL 与 Model 使用同宽标签对齐，行间距 4px；管理模型按钮靠左。

供应商编辑弹窗只提供「确定」保存按钮，启用状态完全由表单中的状态开关决定。


## 个人供应商与全局供应商独立启用（2026-09-27）

状态：已实现。

管理员的供应商 enabled 开关只控制 global 内置模型。普通用户配置个人 Key 即可获取远端候选、添加 user 模型，不要求管理员开启供应商，也不修改全局开关。个人模型只对本人可见，目录、显式选择和默认模型解析均只检查模型自身 enabled 与个人 Key；清除个人 Key 后不可用，不回落到共享凭据。全局模型保留个人 Key 优先于共享 Key 的已有规则，但仍受管理员的供应商开关控制。

数据流：创建接口按 scope 检查全局开关或个人 Key；ChatRuntime._model_api_key 统一解析凭据，core/model_catalog.py 按 scope 判断供应商开关。普通用户远端列表调用只使用个人 Key。连接地址和高级配置仍由管理员维护，不代表个人供应商配置完全复制。开发模拟身份的安全边界不变。

无需数据库改动或清库。运行现有服务后，管理员关闭某供应商，普通用户配置个人 Key 并添加模型，应能在自定义分组选择；另一用户不可见，清除 Key 后不可调用。自动测试覆盖创建权限、目录可用性、默认与显式解析、凭据隔离和远端列表。真实外部模型调用需要有效个人 Key，本次不发送付费请求。

## 空模型初始化与凭据清理（2026-09-27）

全新环境执行 `uv run melonclaw-db-init`，然后执行 `scripts/restart.sh`。预期服务就绪、模型目录为空；登录后配置供应商 Key 并添加模型。无可用模型时 API 返回空默认选择，发送请求得到可读配置提示。现存数据库此次只清理凭据：23 个供应商的共享 Key、API Key Env 和请求头已清空并停用，删除 1 条个人 Key；保留模型、用户和会话记录。版本递增并重启服务使旧 Agent 实例失效。

验证：相关后端测试 79 项通过，Composer 23 项通过；实际 HTTP 检查确认服务 ready、可用模型为零、默认选择为空、凭据状态均未配置。全量检查仍有原有租户种子测试和 3 项供应商弹窗测试失败。

默认模型按归属独立保存：全局一个、每个用户一个个人默认。可用的个人默认优先于管理员内置默认；个人默认不可用时使用内置默认，两者都不可用时优先首个可用内置模型，再选个人模型。配置刷新时聊天选择同步服务端默认，用户仍可在聊天中临时切换。


## 供应商配置收敛（2026-09-28）

状态：已实现。供应商 scope 固定 global，模型 scope 仅 global/user。供应商列表对全部用户可见，移除私有供应商分支。按用户最新决定，保留完整环境变量凭据功能：全局模型优先使用个人 Key，其次数据库共享 Key，最后读取管理员指定的环境变量；个人模型只用个人 Key。历史消息失效模型切换默认的逻辑保持不变。

没有新增 Agent 工具或权限。Key 和请求头仍只写不回读；缺少凭据时模型不可用。环境变量凭据是显式配置能力，不是历史数据兼容；不增加迁移或旧列读取分支。

### 应用表结构变更

恢复环境变量凭据本身不要求重建表：现有数据库已包含 `api_key_env`。但旧 scope CHECK 仍允许 user/tenant 供应商和 tenant 模型；API 已拒绝这些值，若要同步数据库约束，按开发期约定重建以下三表。`db-init` 使用 create_all，不会更新已有 CHECK，单独重跑无效。

需要同步约束时，先停止 Web 服务。在数据库管理工具中连接当前开发数据库后执行以下 SQL，会删除全部供应商、模型配置及个人 Key；不会删除会话和项目：

```sql
DROP TABLE provider_user_keys, model_configs, model_providers;
```

再执行：

```bash
uv run melonclaw-db-init
scripts/restart.sh
```

预期只恢复 23 个停用且无凭据的供应商模板，模型目录为空；登录后重新配置 Key 和模型。仅 DELETE/TRUNCATE 数据不能更新 CHECK 约束。本次代码清理不自动执行数据库删除。

验证覆盖：数据库 scope 约束、API 拒绝私有/租户供应商与租户模型、个人 Key → 共享 Key → 显式指定环境变量的优先级与变量名校验，以及前后端高级参数透传。完整检查使用 `scripts/check.sh`；无需真实外部 API。

恢复后验证：scripts/check.sh 的编译、ruff、锁文件、前端 lint/类型检查和 171 项前端测试通过；后端 255 项通过、6 个 subtests 通过，仅既有租户种子测试失败。本次只读取了数据库列和约束信息，没有执行 DROP 或 db-init。
