# MelonClaw 架构

本文档描述代码的组织方式、依赖方向，以及几条不写在代码注释里的关键取舍。改动模块边界前请先读这里。

## 1. 顶层数据流

```
浏览器（frontend/，React + Vite）
        │  POST /api/conversations/{id}/messages  (JSON)
        ▼
api/            FastAPI 路由、请求校验、附件 multipart 上传、SSE 编码
        ▼
        ▼
core/agent.py   用 create_deep_agent 组装 Agent（模型 + 工具 + middleware + backend）
        ▼
runtime.py      按 Project 或普通 Conversation 解析工作区，按工作区/模型/能力缓存 Agent
        ▼
backend/        CompositeBackend：默认 LocalShellBackend(当前工作区) + 受保护目录与有效 Skill 快照的 /skills/、/skills-user/ 路由
        ▼
output/         把 LangGraph 消息流投影成有序 assistant steps 与 SSE 事件
```

附件采用独立的两阶段数据流：浏览器先向 `api/routes/attachments.py` 上传到当前
Project 或普通 Conversation 的受控 `.attachments/` 目录。需要识别的文件在上传请求中等待 DeepSeek OCR。
本地文本和表格通过解析子进程生成派生 Markdown。按模型能力分流的图片可通过原附件 ID 补 OCR。
发送消息时提交附件 ID，由 `repository/attachments.py` 在消息事务中绑定，并记录本轮使用 OCR 正文的图片 ID。
hydration 按绑定的表示提供受控 Markdown 路径、图片块或 ZIP 引用。出站图片按文件版本缓存并等比缩放，
已失效的附件以可见提示交给模型。 [聊天附件处理](design-docs/chat-attachment-processing.md)

消息准备先检查幂等键并取得会话锁，再构建新运行的 Agent；已结束请求直接回放
业务消息快照。无论有无附件，user/assistant 消息对都由同一事务写入。v3 事件
投影器与执行服务共用一份 assistant steps 快照，用于 SSE 增量与最终落库；
会话锁仍持有到流结束，以保护同一 Checkpoint 的单执行语义。

持久化分两块，职责不重叠：

- `database/` + `repository/`：业务数据（用户、Project、Conversation、消息、审批、用户问题交互、Memory 事件）；首次建库由 `melonclaw-db-init` 创建，保留数据的结构升级由 `melonclaw-db-update` 执行。
- LangGraph Checkpointer：Agent 图状态，与业务表分离。

`tenants` 与 `users` 是一对多关系：`users.tenant_id` 是非空外键，租户角色和状态也直接保存在用户行中，不设成员关联表。服务层从已验证的 JWT 身份读取用户唯一且有效的租户归属，生成 Agent 和 Memory 的运行上下文。Tenant Memory 按这个租户 ID 分区并按用户租户角色授权；Memory 操作会再次核对运行上下文与数据库归属。当前 system/admin 可通过用户管理接口编辑目标用户的租户归属；后续业务请求会重新解析有效归属，不从客户端指定运行租户。

**身份决定租户隔离的实际强度。** `api/app.py` 通过全局 `require_session` 依赖统一保护 JSON、上传、下载和 SSE；请求中的 `user_id` 与 `X-Melonclaw-User` 仅作 JWT 身份断言，不得覆盖服务端身份，附件的 `api/identity.py` 同样读取已验证会话。仅 profile=dev 开放全员显式切换任意有效用户（含 admin），仍不能作为生产用户间权限隔离；共享环境还须隔离 Agent 的文件与 Shell 能力，`LocalShellBackend` 不是安全沙箱。普通业务请求不得用 `tenant_id` 指定运行上下文；管理用户资料的创建／编辑接口可以提交目标 tenant_id，后端验证租户有效性。运行租户始终从服务端用户记录解析。账户与会话流程见下文「用户管理与开发登录」及[登录设计](design-docs/user-management-login.md)。

Conversation 的主键是 `chat_conversations.id`，表内没有重复的 `conversation_id` 列；
消息、审批、用户问题和附件关系表使用 `conversation_id` 外键引用它。Conversation 归属由
`user_id + project_id` 确定：`project_id IS NULL` 表示普通会话，工作区为
`conversations/<conversation_id>`；非空时工作区为项目的 `workdir_path`，同一项目内的会话共享文件，
但仍各自使用独立的消息序列和 Checkpoint。项目和普通会话的附件分别由
`chat_attachments.project_id` 与 `owner_conversation_id` 表示，数据库 CHECK 保证恰有一个归属。
项目与会话各自持久化 `is_pinned` 供侧栏排序；删除使用 `status=deleted` 逻辑删除，读取时过滤已删除项目及其会话，保留跨数据库、Checkpoint 和文件系统的原始数据以避免非原子清理。
会话列表的 `task_only` 筛选沿 API → ChatService → ConversationService → Repository 传递；仓储在排序与分页前限制关联任务非空，继续复用用户、当前租户、会话与项目可见性条件。不按任务是否运行筛选，暂停和归档任务的有效会话仍可读取。
普通会话加入项目时保留会话 ID、消息和 Checkpoint；先把该会话独享工作区里的普通文件、artifacts 和已登记附件复制到目标项目工作区，再在事务中把 Conversation 与附件归属切为项目。移动成功后清理原目录，后续运行只走项目工作区。任务固定会话不支持移入项目；项目内会话不支持再次移动；目标同名文件、运行中的会话和未提交附件会阻止移动。无需新增表列或历史数据兼容读取分支。

模型消息的原始 v3 content-block 事件由 `output/model_activity.py` 按顺序读取：文本及 reasoning 内容增量完整进入脱敏步骤投影，不再删除 `<think>` 块或按正文 JSON 形状判断内部消息。内部工具选择器调用通过公开 `TAG_NOSTREAM` 在 messages 投影源头隔离；阶段由中间件的 `runtime.stream_writer` 发出，`output/events.py` 注册 v3 `CustomTransformer`，仅转发根命名空间的选择工具/等待模型阶段。当前锁定的 LangGraph 1.2.10 不将调用 tags/metadata 挂到消息对象，不能通过消息私有字段识别选择器。工具参数只报告阶段，不把未完成参数当成已执行工具。

## 2. 模块职责

| 包 | 职责 | 典型文件 |
|---|---|---|
| `core/` | 配置、模型目录、模型工厂、提示词、Agent 组装、HITL 审批与用户问题、PTC Interpreter、MCP 配置与脱敏 | `config.py`、`model_catalog.py`、`chat_model.py`、`agent.py`、`hitl.py`、`user_input.py` |
| `database/` | 连接、表结构定义、建表与完整性校验、常量 | `schema.py`、`schema_validation.py`、`constants.py` |
| `repository/` | 业务数据的读写、事务边界、会话锁、上下文与用户解析 | `repository.py`、`conversations.py`、`attachments.py`、`user_interactions.py`、`locks.py`、`bootstrap.py` |
| `parsers/` | 附件扩展名/MIME/容器安全校验，以及受控文档到 Markdown 派生文件的解析 | `validation.py`、`documents.py` |
| `storage/` | 当前工作区内附件原文、派生文件与临时文件的受控路径映射和发布 | `attachments.py` |
| `services/` | 用例编排：执行、执行收尾、用户问题恢复、会话、技能、运行时资源管理 | `execution.py`、`execution_finalize.py`、`user_input_execution.py`、`runtime.py`、`chat.py`、`skills.py` |
| `api/` | HTTP 边界：路由、Schema、错误映射、SSE 编码、应用生命周期 | `app.py`、`routes/*`（含 `user_input.py`）、`schemas.py`、`sse.py` |
| `output/` | 通过当前 Deep Agents v3 事件投影提取模型完整文本，并把根 Agent 的每次 AIMessage 投影成有序 assistant steps；子 Agent 保留任务卡事件 | `events.py`、`model_activity.py`、`assistant_steps.py`、`formatting.py` |
| `memory/` | Global / Tenant / User 三级长期记忆的中间件、工具与服务 | `service.py`、`middleware.py`、`tools.py` |
| `middleware/` | Agent 中间件：文件操作顺序、工具动态选择、用户提问批次护栏、任务预算与运行控制 | `file_ordering.py`、`tool_selection.py`、`user_input_guard.py`、`task_budget.py`、`summarization.py`、`todo.py` |
| `backend/` | Deep Agents Backend 的构造与路径路由 | `factory.py` |
| `tool/` | 注入 Agent 的工具（DeepSeek 联网搜索与网页读取、MCP 目录、受控 Skill 安装） | `tools.py`、`search.py` |

## 3. 依赖方向

**每个包只允许依赖下表“可以依赖”列中的包。** 反向依赖由 `tests/test_architecture.py` 强制，违反会让检查失败。

| 包 | 可以依赖 | 禁止依赖（当前测试强制） |
|---|---|---|
| `core/` | 任意包，含组装点（见下方小节） | 无 |
| `database/` | `core/` | `repository/`、`services/`、`api/`、`output/`、`memory/`、`tool/`、`middleware/`、`backend/` |
| `repository/` | `core/`、`database/` | `services/`、`api/`、`output/`、`memory/`、`tool/`、`middleware/`、`backend/` |
| `output/` | `core/` | `database/`、`repository/`、`services/`、`api/`、`memory/`、`tool/`、`middleware/`、`backend/` |
| `tool/` | `core/` | `database/`、`repository/`、`services/`、`api/`、`output/`、`memory/`、`middleware/`、`backend/` |
| `memory/` | `core/`、`database/`、`repository/` | `services/`、`api/`、`output/`、`tool/`、`middleware/`、`backend/` |
| `backend/` | `core/`、`memory/` | `database/`、`repository/`、`services/`、`api/`、`output/`、`tool/`、`middleware/` |
| `middleware/` | `core/`、`memory/`、`output/` | `database/`、`repository/`、`services/`、`api/`、`tool/`、`backend/` |
| `parsers/` | 无业务包 | `core/`、`database/`、`repository/`、`services/`、`api/`、`output/`、`memory/`、`tool/`、`middleware/`、`backend/`、`storage/` |
| `services/` | `core/`、`database/`、`repository/`、`output/`、`memory/`、`backend/`、`middleware/`、`tool/`、`parsers/`、`storage/` | `api/` |
| `storage/` | 无业务包 | `core/`、`database/`、`repository/`、`services/`、`api/`、`output/`、`memory/`、`tool/`、`middleware/`、`backend/`、`parsers/` |
| `api/` | 以上全部 | 无 |
| `main_web.py`（Web 入口） | 任意包 | 无 |
| `main_db_init.py`、`main_db_update.py`、`main_admin_password.py`、`main_resources.py`（管理入口） | 按职责依赖 API 以外的包 | `api/` |

补充规则（同样由测试强制）：**`api/` 只能被入口文件 `main_web.py` 导入**，业务包不得反向引用 Web 层。

命令注册与 API 导入许可是两个独立集合。`pyproject.toml` 注册以下五个命令；架构测试的 `API_ENTRY_FILES` 仅允许包根目录的 `main_web.py`，其他目录中的同名文件也不获得例外。

| 命令 | 入口模块 | 职责 |
|---|---|---|
| `melonclaw-web` | `main_web.py` | Web 服务组装与启动 |
| `melonclaw-db-init` | `main_db_init.py` | 首次建库、种子和持久化组件初始化 |
| `melonclaw-db-update` | `main_db_update.py` | 执行已登记的保留数据升级 |
| `melonclaw-admin-password` | `main_admin_password.py` | 交互设置管理员密码 |
| `melonclaw-resources` | `main_resources.py` | 内置资源安装与持久目录迁移 |

### 为什么 `core/` 不是“最底层”

`core/` 里有两类内容，不要混为一谈：

- **基础件**：`config.py`、`defaults.py`、`model_catalog.py`、`chat_model.py`、`mcp_config.py` —— 只被别人依赖，不依赖业务包。
- **组装点**：`agent.py` 是 Agent 的装配处，会主动引用 `backend/`、`memory/`、`middleware/`、`tool/`；`hitl.py` 会引用 `output/formatting.py` 做脱敏。

所以本仓库不采用 `core → repository → services → api` 这种完全单链表，而是逐包声明禁止边。新增包时，先在 `tests/test_architecture.py` 的 `FORBIDDEN_IMPORTS` 里补一行，再写代码。

## 4. 横切关注点只从固定入口进入

| 关注点 | 唯一入口 | 说明 |
|---|---|---|
| 配置与环境变量 | `core/config.py` 的 `Settings` / `load_settings()`；Web 监听地址由同文件 `web_bind()` 解析 | 新增环境变量在这里解析，不要在各模块直接读 `os.environ`（`MELONCLAW_*` 风格）。`MELONCLAW_HOST` / `MELONCLAW_PORT` 只服务进程绑定，不进入 Settings，也不创建数据目录 |
| 模型与能力 | `core/model_catalog.py` 的 `ResolvedModel` + 目录条目转换；模型目录以 `model_providers` + `model_configs` 两张表为唯一事实来源（供应商持有 base_url/api_key/模型列表端点，模型经 `provider_key` 引用供应商；db-init 只写无凭据、未启用的供应商模板，不创建模型），runtime 按 user_id 查可见行并联查供应商行 | 请求只能提交目录模型 ID（`custom:` 前缀），不接受前端传任意模型名；`model_providers.api_key` 只写不回读，模型停用或缺少有效 Key 时不可用，全局模型另受供应商开关控制；模型／供应商／Key 写入统一发布内存资源版本，下一次准备时旧 Agent 缓存失效；DB 无可用行时返回空选择并提示配置；Agent 构建必须显式接收数据库解析后的模型 |
| 模型实例 | `core/chat_model.py` 的 `build_chat_model()` | 所有 provider（含 custom）统一经此构造 |
| MCP 服务定义 | `services/mcp.py` 的运行时装配（配置通过 Web 页面写入 `mcp_servers` 表） | 无 MCP 时必须能正常启动；user scope 仅 http/sse 且禁止 `${VAR}`，global scope 允许 stdio 与 `${VAR}`（管理员发布） |
| 长期记忆 | `memory/` 的 `MemoryService` | 写入必须经过它的固定工具与审计，不直接写 Store |
| HITL 与副作用工具 | `core/hitl.py` 的审批清单 + `FilesystemPermission`；用户问题由 `core/user_input.py` 的 `interrupt()` 进入同一 Checkpoint | write_file、edit_file、delete 免逐次审批，仍受 FilesystemPermission 路径规则限制；固定工具审批保留 Skill 安装、MCP 测试和安装、环境变量确认；Shell 沿用受信任本地开发免审批例外；缺少关键用户决策时 Agent 可暂停等待回答；审批恢复必须绑定当前 `approval_batch_id` 与 `assistant_message_id`；用户问题的唯一出口是一次真正的 `Command(resume=...)`，答案可以是选项/文本，也可以是 `{"type": "cancelled"}`（用户跳过或 TTL 过期后由 `services/user_input_execution.py` 代答），只改 `user_interactions` 状态不会解除 Checkpoint 挂起；答案已收但本轮没跑完时账本会被标成 `recovery_required` 并且**禁止自动重放**（不知道副作用执行到哪一步），此时历史表现为失败，唯一的解锁入口是用户发新消息——那时服务层会先代答取消、把 Checkpoint 叫醒收尾 |
| 业务数据持久化 | `repository/` 的 `BusinessRepository` | `services/` 不直接写 SQL |
| Skill 资源 | `services/skills.py` 解析正文；`skill_state.py` 统一有效状态；`skill_import.py` 导入更新；`skill_operations.py` 文件日志恢复；`skill_index.py` 索引重建 | 正文在文件系统，DB 保存归属、启停、版本和来源。持久草稿与内容提交受同数据根文件锁保护；更新保留 ID/偏好，删除先隔离目录。管理、Picker 和 Agent 共用有效状态；`skill_snapshot.py` 固定有效目录内容与摘要，`middleware/skill_refresh.py` 每轮刷新 Checkpoint 摘要。详见 [Skill 生命周期](design-docs/skill-lifecycle.md)。 |
| Skill/模型资源管理 | `services/resource_service.py` | 权限矩阵在这里强制执行：global 资源仅 admin/owner 可管理，user 资源仅创建者（含 admin）可动 |
| HTTP 身份 | `api/auth.py` 的 `require_session()` | 统一验证 JWT、来源和页面身份断言；附件由 `api/identity.py` 读取已验证身份。管理授权按当前 system/admin，业务入口在共享账户锁内要求租户启用，服务层校验资源归属。写操作结束后 `api/audit.py` 追加审计文件，不写业务表；写入失败只记警告，并按配置后台通知，见 [安全审计日志](design-docs/security-audit-log.md) |

投诉举报走 `api/routes/reports.py` → `services/reports.py` → `repository/reports.py`。匿名提交和查询是公开路径，其余请求仍经 `require_session()`。管理操作挂在 `/api/admin/reports`，沿用 system/admin 检查。知识库整库停用继续走原知识库接口。详见 [投诉举报与反馈](design-docs/reports.md)。

凭据相关有一条硬规则：`DEEPSEEK_API_KEY`、`DATABASE_URL`、`LANGFUSE_SECRET_KEY` 等应用自身凭据**不能**注入给 Agent 执行的命令；系统基础环境与禁止注入规则集中在 `core/defaults.py`，Skill 业务变量只由数据库共享／个人资源解析，不从 `.env` 透传。

### 聊天安装 Skill 与教程

聊天生成入口 `prepare_skill_creation` 与安装工具共用 provider 和确认审批。`services/skill_creation.py` 校验至多 32 文件、64000 UTF-8 字节的 SKILL.md/文本参考并打包；`services/skill_install.py` 绑定身份、会话和 generated 来源，复用 SkillImportService 与 SkillOperations。内置 skill-creator 负责内容提炼，服务端按 admin/owner global、member user 决定目录与数据库归属，不增加 HTTP 回环或数据写入旁路。详见 [聊天生成设计](design-docs/chat-skill-creation.md)。

`ChatService` 向 runtime 注入 `ChatSkillInstallService` provider，`core/agent.py` 使用 `tool/skill_install.py` 的协议创建准备和确认工具。身份由 `ToolRuntime` 的 `AgentContext.user_id/tenant_id/conversation_id/project_id` 提供，每次执行重新查询用户、会话与项目；模型不提供身份。聊天与页面共用导入服务，confirm 通过 `core/hitl.py` 审批，按草稿核对完整清单及会话，不进入 PTC。ZIP 附件使用 archive 类型，仓储绑定时无需文档解析或视觉模型，hydration 仅传附件 ID。安装并启用在原操作日志 ready 提交中完成，下一轮快照生效。教程通过现有内置索引默认启用。详见 [设计与边界](design-docs/chat-skill-install.md)。

聊天批量同步由 `prepare_skill_batch` / `confirm_skill_batch` 复用原 Skill 导入服务：公网 HTTPS 下载按 DNS 校验并固定连接 IP，受控展开 `skills/` 子 ZIP；按当前 scope 与包内 name 自动更新，同名不同内容显式冲突，相同内容跳过。保留资源身份、启用状态和原提交/恢复/资源失效边界；批量各项独立提交，正式写入须 HITL，均不进 PTC。混合输入先提取并脱敏多项环境变量，复用 environment_operations 保存同范围 change 列表，一次审批、同一事务保存与幂等回执；技能与变量独立提交。详见[聊天安装设计](design-docs/chat-skill-install.md)。

### Agent 运行控制

`core/agent_controls.py` 是主/子 Agent 运行中间件组装入口，配置统一来自 `core/config.py`。模型窗口从 `model_configs.context_window` 进入 ResolvedModel，默认 1,000,000；压缩阈值为当前窗口乘 `.env` 的比例（默认 0.8），主/子 Agent 与上下文观测使用同一窗口。API 和资源管理 UI 支持创建/编辑，配置版本递增使缓存失效。`middleware/summarization.py` 按官方 SummarizationMiddleware 名称替换默认实例，在官方计数前复用工具池的请求过滤；摘要算法、offload 和溢出恢复仍由官方实现负责。官方 ModelRetryMiddleware、ModelCallLimitMiddleware、ToolCallLimitMiddleware 和 TodoListMiddleware 分别负责瞬时重试、调用限额和任务清单。Todo 关闭时由 `middleware/todo.py` 显式替换默认 Todo 插槽，不注入工具或提示词，并清空旧清单；主/子 Agent 均覆盖模型 profile 的自动注入。`middleware/task_budget.py` 用私有状态把官方 thread 计数按业务用户消息分段，审批恢复不重置。供应商 SDK 重试关闭，模型边界保留 ContextOverflowError 并禁止已输出流的重放。

`middleware/usage_observation.py` 估算模型出站上下文；`core/model_usage.py` 以每次执行独立 callback 采集包括内部调用的 token 用量。`output/events.py` 负责安全事件输出，`services/execution_trace.py` 负责按调用 ID 合并并沿既有 display_metadata 持久化。没有新增表或业务数据旁路。详见 [运行控制设计](design-docs/agent-runtime-controls.md)。

## 5. 关键取舍与历史教训

### 5.1 `LocalShellBackend` 不是安全沙箱

默认后端允许本地文件和 Shell 能力，只适合本机开发。面向共享或不受信任用户部署前，必须替换为受控 SandboxBackend 或独立解析服务。这一条在 README 的“使用边界”里也对用户可见。

Deep Agents 0.7 对带 `execute` 能力的 backend 不支持默认路径上的
`FilesystemPermission`。因此 `/.attachments/` 和 `/.artifacts/` 被路由到不提供
Shell 执行的 `FilesystemBackend`，附件原文、派生目录和运行时 artifacts 的工具权限
可以继续生效；`execute` 始终只委托给默认工作区 backend。当前 `core/hitl.py` 的 write_file、edit_file、delete 免逐次审批，Shell 沿用免审批开发例外；文件工具仍受上述路径规则限制，Skill 安装、MCP 测试和安装继续审批。主/子 Agent 共用文件工具指引，不通过 ask_user 请求文件调用批准，缺少关键任务信息仍可提问。仅限受信任开发环境。这个
路由不能把本机 Shell 变成沙箱，生产部署仍需替换默认 backend。

### 5.2 不要依赖上游框架按类型推断能力

DeepSeek / MiniMax 都通过 `ChatOpenAI` 适配。`deepagents` 会把 `ChatOpenAI` 这一类**按类名**判定为“能接受非 PDF 文件块”，从而绕过按模型能力（`ModelProfile`）做的门控；而 `ChatOpenAI` 的能力表只收录 `gpt-*` 系列，我们的模型名拿不到能力声明，所有门控默认放行。

结论：**涉及模型能力的判断，必须由本仓库显式声明，不能依赖框架自动推导。** 当前实现与验证依据见[附件模型能力边界](design-docs/chat-attachments.md#模型能力与请求副本)。

### 5.3 内容块转换是框架职责，路由是应用职责

标准内容块到各家 provider 请求体的转换由 LangChain 提供；“该不该发这种块”和“被拒之后怎么降级”属于应用层职责。第三方非标准推理协议是例外：管理员通过供应商高级配置内的格式下拉框或扩展 JSON 声明协议，两者共用 `extra_config._melonclaw.reasoning_format`。`core/reasoning.py` 解析该声明，省略时默认 `openai`，本地 `_melonclaw` 字段在出站前剥离；`core/chat_model.py` 同时适配响应增量、完整消息和下一次请求。原始推理字段保存在 Checkpoint，展示只使用脱敏限长的有序内容块；签名、加密片段不进入 UI，也不从展示快照重建模型历史。

### 5.3.1 回复流畅性与会话内工具选择

`core/tool_catalog.py` 定义请求 ID、目录摘要和 Checkpoint 工具池；`tool/tool_discovery.py` 的 `find_tools` 提交能力需求或真实工具名称，不执行候选工具。提供 `tool_names` 时直接校验当前授权目录，未知名称整批拒绝；否则委托官方选择器。before_model 按稳定消息 ID 替换对应 tool_call_id 的 queued 回执，向模型报告最终加载状态、已加载名称和当前池；并行请求按最终容量结果报告，不把加载视为业务调用成功。`middleware/tool_selection.py` 在 before_model 统一处理本消息预算内的待处理请求，委托官方 `LLMToolSelectorMiddleware`，其 handler 只收集工具名，不调用主模型。选择参数使用需求文本而非原始用户消息。池与已处理 ID 在模型执行前落入 Checkpoint；跨消息复用，审批恢复不重新选择。容量有限，最近选择/实际请求的工具优先保留。目录摘要覆盖已授权工具名称、描述和 schema；变化清池，旧请求不自动重放。共享 Project Agent 实例不保存会话状态，主/子 Agent 显式装配工具池 middleware，PrivateStateAttr 阻止父子池传播。超时、结构化输出失败保留旧池且发出降级阶段，不输出异常细节；取消向上传播。不扩大 MCP 白名单、HITL 或 PTC。池容量、单次扩充、每消息预算和超时均从 `core/config.py` 进入，详见 [按需工具池](design-docs/on-demand-tool-pool.md)。

`output/events.py` 并发排空各 ToolCallStream，快工具结果不等待慢工具。生成调用仅标记 queued；观测工具执行才记开始时间，耗时用单调时钟。`services/execution_trace.py` 保存阶段耗时及子 Agent 展示轨迹；准备阶段仍在 SSE 之前，错误保持 HTTP 语义。`output/stream_redaction.py` 在连续增量之间识别凭据，普通正文不作整段缓冲。SSE 与最终 `assistant_steps.content_blocks` 同时保留正文/推理顺序；细节见 [回复流畅性与推理协议](design-docs/response-fluency.md)。

### 5.4 消息长度的双重约束

- 业务侧：`MessageRequest.content` 限制 1–12000 字符。
- 框架侧：`read_file` 等工具结果有 token 阈值，超限会被截断或改成文件引用。两端都要考虑，不要只按其中一侧设计。

### 5.5 错误响应的脱敏边界

`api/errors.py` 的 `error_response` 会对异常文本调用 `output/formatting.py` 的 `sanitize_text`，但它只脱敏环境变量、Token 和授权字段的值，**不管文件系统路径**。而 `shutil`、`open()`、`os` 抛出的原生异常恰好都带完整绝对路径。所以任何会被交给 `error_response` 的异常都不得携带宿主机路径：涉及文件的写操作要在调用前显式检查并抛业务异常（如下载 Skill 前先确认源目录存在），不要让原生异常冒到 API 边界。同理，用户能自己纠正的失败（如已选技能在执行前被移除）应通过异常上的 `status_code` 映射成 4xx，而不是落到默认的 500 让前端当成服务端故障重试。

## 6. 数据库变更流程

`database/schema.py` 是新数据库当前结构的唯一事实来源；数据库维护分为首次／重建初始化与保留数据升级。

1. 首次上线或重大变更决定放弃历史数据时，在空数据库执行 `uv run melonclaw-db-init`。它用 `metadata.create_all` 建业务表，并准备 Checkpoint、Memory Store 和初始种子；不主动清库。
2. 新功能需调整现有数据库且保留历史数据时，新增有序升级脚本到 `database/updates/`，通过 `uv run melonclaw-db-update` 执行。升级 ID 与成功步骤保存在 `melonclaw_schema_updates`；DDL、必要的数据补齐和该 ID 在同一事务提交。重复运行会跳过已成功步骤。升级须校验前置表／数据状态，失败时事务回滚，不在业务读取路径添加旧结构兜底。
3. 服务启动时 `verify_schema` 只校验，不自动建表或升级。缺少基础用户／租户表时提示 db-init；现有数据库缺少新对象或列时提示 db-update。

`metadata.create_all` 只建缺失对象，从不 ALTER 已有表。保留数据升级必须显式维护版本化脚本；完成升级后由 `verify_schema` 对 `BUSINESS_TABLES` 逐表比对当前列。**业务表和资源表都必须登记在 `database/constants.py` 的 `BUSINESS_TABLES`**：漏登记会让漂移延迟到其他数据库操作才暴露。新数据库仍直接按 `schema.py` 创建。

清库会丢掉两类“看起来在代码里、实际只在数据库里”的东西：**结构**从 `schema.py` 创建；**索引类数据**由 db-init 从磁盘或仓库种子重建（Skill 索引来自 `data_root/skills/`）。MCP 配置由 Web 页面写入数据库，清库后需要重新配置。新增任何“DB 只是投影”的资源时，都要同时给出重建入口。重大变更选择清库时，要明确数据影响并由操作者执行清理；init 本身不得删除数据库内容。

若重建数据库是为了改变用户的租户映射，先备份需要保留的文件，并清理 `MELONCLAW_WORKSPACE_DIR` 指向的旧工作区（未配置时为 `~/.melonclaw/workspaces`），再初始化数据库。建表命令不会清理工作区；沿用旧目录会留下与新数据库无对应记录的文件。工作区路径中的 UUID 和目录层级不是租户安全边界。

### 供应商高级参数

供应商持有 `api_key_env`、`request_headers`、`extra_config`。配置校验集中在 `services/provider_config.py`；全局模型的凭据由仓储按个人 Key → 数据库共享 Key → 指定环境变量解析，环境变量读取统一走 `core/config.py`。这只补充凭据，不从环境变量生成模型。供应商的 scope 在数据库与 API 均限定为 `global`，模型限定为 `global/user`，不保留私有供应商或租户模型分支。`core/model_catalog.py` 把高级参数带入 ResolvedModel，`core/chat_model.py` 分别通过 default_headers 和 extra_body 注入；远端模型列表请求复用 request_headers。请求头值与 Key 不进入公共响应，扩展请求体不可覆盖模型/消息/工具/流协议或凭据字段。

普通用户配置个人 Key 后可独立添加和使用个人模型，不受供应商全局 enabled 开关影响；管理员开关仅控制内置模型。个人模型仅使用个人 Key，清除后不可用，不借用共享 Key。连接地址仍由管理员维护。

### MCP 两层配置与连接边界（2026-10-01）

MCP 管理从 `services/mcp_management.py` 进入，持久化由 `repository/mcp.py` 完成；运行时
仍只从 `services/mcp.py` 读取数据库配置。配置与个人偏好分别存入 `mcp_servers` 和
`mcp_user_preferences`。先确定个人覆盖来源，再判断启用，停用个人项不回退全局。
headers/env 字面值由 `core/mcp_credentials.py` 按明文存入数据库；管理 API 不回传凭据值。
数据库查询、转储与备份可读到这些凭据，应限制其读取范围。
缓存判断使用统一内存资源版本；配置／偏好写入（含删除）提交后发布版本，未命中才读快照；失败工具发现不永久缓存。
已启用且白名单允许的外部 MCP 工具免逐次审批，不进入 PTC；`core/hitl.py` 仅保留固定敏感工具
清单，聊天安装/测试 MCP 仍审批。主/子 Agent 提示词禁止用 `ask_user` 或文字请求批准 MCP 调用。
命名空间中的连接指纹防止旧调用使用新连接。JSON 原文只在前端导入，不进入数据库或模型。
工具白名单选择统一使用 `core/mcp_config.py` 的 `select_mcp_tool_names`，按目录交集提供工具，
缺失项警告不扩大授权。管理发现由 `ChatRuntime.mcp_discovery`（`services/mcp_discovery.py`）
调度，权限/版本校验先于共享任务与脱敏目录缓存；草稿和 stdio 不缓存，Agent 独立发现并按启用状态与白名单提供工具。
完整权限、API、连接恢复边界与验证见 [MCP 两层设计](design-docs/mcp-two-layer.md)。

### 聊天安装 MCP

消息准备阶段由 `services/mcp_chat_config.py` 提取 MCP JSON，数据库 `mcp_install_drafts` 保存受控配置，消息、Checkpoint 和模型只接收草稿引用。`ChatService` 注入 `ChatMcpInstallService` provider，`tool/mcp_install.py` 提供准备、审批测试和审批安装工具。身份从 ToolRuntime 注入并重新校验用户、会话和项目，不允许模型指定用户。聊天安装与资源页统一按当前角色决定范围：管理员/owner 为系统共享，普通用户为个人；`McpManagementService.prepare(personal=False)` 复用管理校验，测试和确认时重新核验角色与审批范围一致。

正式安装由 `repository/mcp_install.py` 在同一事务写配置、个人项的偏好和草稿结果，行锁保护并发幂等；按 scope/owner/slug 更新同名原记录，保留未提供字段、原身份与偏好；目标版本、合并快照、当前角色及会话归属在提交事务内重验。明确偏好变更另比较原偏好，更新与无凭据幂等回执同事务；测试状态只绑定当前草稿，不扩展白名单。测试和保存进入 HITL，均不进入 PTC，下一轮沿用现有 MCP 快照摘要生效。草稿有效期 24 小时，过期访问拒绝，暂存新配置时清理；审批草稿提交成功后擦除敏感快照，payload 只保留无凭据 result 回执；纯 URL 凭据轮换也按变更处理。详见 [聊天安装设计](design-docs/chat-mcp-install.md)。


### 聊天结果组件与成果读取

结构化结果使用原有 assistant 正文中的 `melon-result` JSON 围栏，不另开 SSE 或持久化旁路。提示词走 `core/prompts.py`；前端只接收固定类型、有限大小的数据，使用固定组件。结果格式校验在浏览器完成，文件元信息和读取权限由服务端核验，不能把格式校验等同于数据事实核验。

`services/results.py` 通过 `ConversationService` 和 `repository/` 解析有效身份、会话及项目归属，通过 `ChatRuntime.workspace_dir` 定位现有工作区；`storage/results.py` 只读 `/outputs/` 交付文件，目录 fd 与 NOFOLLOW 避免符号链接替换。HTTP 内容默认下载、禁止嗅探、sandbox CSP；预览类型由实际内容检查决定。没有新增 Agent 工具，文件生成在原有路径与权限范围内免逐次审批；LocalShellBackend 的非沙箱边界仍有效。详见 [聊天结果组件](design-docs/chat-result-components.md)。

`services/result_index.py` 从已完成最终回复的 Markdown 语法提取明确文件／图片交付，history 和 completed（含幂等回放）使用同一派生 `artifacts` 字段；文件卡片、正文链接与会话产物列表共用引用。`ResultFileService.index` 经 repository 直接读取 `conversation_artifacts` 交付投影，按路径／附件 ID 合并最近来源，不加载消息正文或扫描工作区。最终正文的引用由 services 解析，repository 在完成回复的 CAS 事务中同步写入投影；以消息 seq 阻止迟到旧交付覆盖新来源。`melonclaw-db-init` 从已完成正文流式、原子重建投影，读取路径不做历史回退。文件仍读取当前内容，不保存版本快照。HTML 使用独立只读预览响应，UTF-8／2 MB 核验、HTTP 与 Blob 文档 meta CSP、无同源权限 sandbox；普通 content 的 HTML 下载边界保持严格。具体流与限制见 [对话产物](design-docs/conversation-artifacts.md)。

文件浏览器复用 `ResultFileService` 的身份与实际工作区解析。`storage/workspace_files.py` 提供按目录读取、5,000 项扫描上限、分页和普通文件 fd 读取；拒绝隐藏路径、符号链接与特殊文件。`storage/results.py` 在同一读取机制上保留成果 `/outputs/` 引用约束。`api/routes/files.py` 的 `/files` 目录／内容接口不扩大 Agent 成果协议权限，内容与 HTML 策略复用既有响应构造。附件列表只经 `repository/attachments.py` 按实际项目／会话与用户查登记信息，不枚举内部物理目录。文件存在与完成回复的交付记录分别读取，不增加表或快照。详见 [文件浏览器](design-docs/workspace-file-browser.md)。


## 用户管理与开发登录

参见[用户管理与登录设计](design-docs/user-management-login.md)和[JWT 登录](design-docs/jwt-login.md)。users/tenants 维持一对多。`auth_sessions` 不再参与登录。账户用例位于 services/accounts.py，SQL 与并发边界位于 repository/accounts.py；API 统一依赖覆盖下载、SSE、上传、审批和普通 JSON 请求。请求中的 user_id 仅作令牌身份断言，不能覆盖服务端身份。管理资料的 tenant_id 是目标字段，不是运行上下文覆盖参数。

仅 profile=dev 开放免密登录和全员显式切换用户（含 admin）；非 dev 隐藏入口并拒绝对应接口。租户记忆不迁移，个人数据按用户所有权保留；Agent 缓存键增加租户维度。SSE 投递与业务执行生命周期分开，浏览器断开后执行继续，服务关闭才取消。账户变更使用 PostgreSQL 全局共享／独占事务锁与未完成助手消息检查，暂不追求管理操作的高并发吞吐。

短信注册、登录和首次绑号仍走账户服务与账户仓储。阿里云号码认证调用在数据库事务外，核验通过后的建户、登录和绑号进入短事务。注册的租户、账号、手机号绑定和核验记录同事务提交。持久记录独立于短期验证请求，管理员通过现有认证入口查阅脱敏详情。短信默认关闭，密码登录继续可用。详见[短信注册与登录](design-docs/sms-registration-login.md)。

公开登录配置额外返回运营主体名称和申请邮箱，供无需登录的隐私说明页使用，不返回短信凭据。详见[隐私说明](design-docs/privacy-notice.md)。


账户来源白名单统一从 `core/config.py` 解析，应用组装时保存快照供 CORS 与写请求校验共用。白名单只放行同站跨域，跨站写入继续拒绝，Cookie 保持 Strict。公开 `/api/ready` 只读取 `manager.ready`，以 200/503 表达初始化状态，不暴露内部错误；登录配置接口不承担就绪判断。详见[账户 review 修复](design-docs/account-review-fixes.md)。

## 用户文件与部署边界

平台资源默认位于 `~/.melonclaw/data/`（`MELONCLAW_DATA_DIR`），用户和共享 Skill 正文、导入草稿、快照都在此根下。仓库 `.data/skills/shared/` 仅是内置模板，首次安装复制到数据根；升级不覆盖已有内容。工作区默认位于 `~/.melonclaw/workspaces/`（`MELONCLAW_WORKSPACE_DIR`），包含生成文件、附件原文和派生文本及 `.artifacts`。Memory、Checkpoint、业务记录和文件索引在 PostgreSQL。代码部署与三类持久数据独立；迁移时停服完整复制文件目录并切换配置，保持相对路径和数据库，无旧路径读取兜底。运行时不会自动迁移旧目录或删除文件。详见 [持久文件边界](design-docs/persistent-user-files.md)。

部署操作通过 `melonclaw-resources` 显式进入 `storage/deployment.py`，只在停服期间复制和校验目录，不覆盖不同内容、不删除源文件、不写数据库。模板先复制到临时目录再发布，已有同名 Skill 整体保留。配置入口拒绝把运行数据根和工作区放进当前代码目录。Linux 服务器布局、systemd/Nginx 与迁移步骤见 [部署说明](deployment.md)；`ops/` 仅包含可安装模板，不含运行数据和凭据。


## 定时任务调度与执行分支

`services/task_resources.py` 在每次真正执行时按已校验身份读取当前可见资源，复用 `skill_state.evaluate_skills`、`SkillOperations` 文件锁和 `services/mcp.py` 的有效来源/凭据/白名单规则。构建前后核对进程内资源版本、有界重试，资源只保留本次运行的路径和连接配置，不复制 Skill 内容、不缓存后台 Agent 或在任务表保存资源快照。

`core/agent.py` 的 unattended 分支组装受限主/子 Agent；`core/task_agent.py` 复用 `tool/tools.py` 的 `build_mcp_tools`，不引入普通聊天管理工具。`backend/task_skills.py` 只提供有效单个 Skill 的虚拟只读路径，通过逐层 O_NOFOLLOW 和普通文件/大小检查拒绝符号链接及主机路径，支持 ls/read_file 和框架读取 SKILL.md，不提供写入、Shell 或长期 Store；其他操作继承 `TaskReadOnlyBackend` 拒绝策略。关闭自动落盘，上下文溢出明确失败。

`middleware/task_policy.py` 在工具目录、整批响应和调用入口校验权限，授权名称来自本次真实 MCP 装配，不根据前缀放行。主/子 Agent 各有私有工具池，共享 `TaskRunControl` 的调用预算、发现额度和停止标志；`ToolPoolMiddleware` 可选接收后台 control，选择器内部模型调用同样扣减模型预算，发现额度耗尽返回 exhausted，不因子任务重置。普通聊天仍保持原有每消息发现语义。每轮提示词包含北京时间、计划时间及缺失后跳过并正常收尾的要求。资源缺失的正常说明可 succeeded，预算耗尽或超时为工程失败；有限目标仍由 complete_task 显式判断。完整边界见[当前资源与执行保护](design-docs/task-resources-and-execution-guards.md)。

`core/task_schedule.py` 仅处理北京时间四种计划，错过/重叠直接跳过；`core/task_snapshot.py` 只定义任务要求和历史标识，不保存权限或模型摘要。TaskService 负责表单/对话共用保存和当前配置运行，repository 的短事务负责入队、计划推进、领取、取消和原子终态。Web 生命周期内的 APScheduler 负责周期扫描和有界异步执行，复用数据库与 Checkpointer，由 repository/task_delivery.py 把执行状态和结果同事务交付到固定会话，不启用任务 Memory。创建/修改设置记录用户实际操作，每次触发仅追加助手消息。repository/task_history.py 固定已完成消息来源，core/task_history.py 统一历史投影和相邻文本合并，services/task_history.py 负责有界摘要与原文读取，后台和追问共享业务历史。模型统一通过 services/model_resolution.py 解析，管理事务显式复用同一连接，避免嵌套取连接造成池耗尽。

任务只有 tasks、task_runs、task_operations 三张表。tasks.conversation_id 通过所有者复合外键绑定唯一普通会话；复用 chat_messages 保存用户实际设置与每轮单条助手交付消息，不增加任务表。普通聊天按 task_run 元数据排除后台占位的审批/遗留恢复；两种执行不共用 Checkpoint。启用状态只存在 tasks.status；独立通知接口、已读字段与索引已移除。主 Agent 正文形成有界部分结果，无独立过程事件存储；运行快照冻结入队定义用于执行与历史解释，重新运行使用当前定义与有效模型。数据库部分唯一索引、配额短事务保护单活动运行；单实例 advisory 会话锁阻止重复执行器，执行状态写入复用持锁连接，断线后不重连接管。账户变更仍检查活动任务，Project 不再关联任务。

普通聊天通过 ChatRuntime.task_history_provider 注入 TaskChatService，middleware/task_context.py 在请求副本投影固定历史并校验归属/摘要；历史来源和早期摘要存入现有用户消息元数据，后台来源存入 config_snapshot，不增加表或关联字段。后台执行与追问的 Checkpoint 独立；同角色普通文本只在请求中合并，本轮工具链保持。历史过长时复用来源未变的摘要和近期正文，read_task_history 只读本轮绑定原文。完整成功结果才推进进度。主 Agent 的 complete_task 只记录内存完成意图，由持锁执行器在成功终态同事务暂停同版本任务；子 Agent 无此权限。会话追问统一使用累计历史，无单份报告引用 API 或参数。

ChatRuntime.task_configuration_provider 注入 services/task_configuration.py，由 tool/task_configuration.py 的 configure_task 先读取实际配置/模型目录，再保存用户明确要求的部分更新。TaskService.save_definition 是表单与聊天共用的保存入口；repository/task_configuration.py 在任务事务内复核真实用户消息、当前非项目会话与租户，TaskService 复用事务连接解析模型。expected_version 与任务幂等账本防止覆盖新配置和重复提交；新建绑定当前会话，聊天来源使用真实消息，表单继续插入设置摘要。该受控工具常驻主 Agent，子 Agent 工具列表移除、后台不装配、PTC 不桥接；不能指定任意目标、不立即运行、不改已入队快照，无新增表或兼容分支。

任务新建省略模型时，通过 ModelResolutionMixin.resolve_system_default_model 读取 scope=global 且 is_default=true 的管理员默认，并使用当前用户的正常凭据链解析；保存明确 model_id。仓储默认查询与模型解析共用管理事务连接，无可用系统默认时明确报错，不使用普通聊天的个人默认、当前选中模型或第一条可用模型。表单默认选择采用同一全局默认条件；已有任务编辑不重新选择默认，默认切换不影响已提交操作的幂等重放。

`20261010_task_center` 为已有应用一次创建当前完整三表，新库直接使用 schema.py。不维护未发布旧任务结构的迁移或历史回填；当前结构保留数据，结构不一致时停服并显式重建任务相关表，不动账户、模型和普通聊天。任务随 Web 停止，正常退出标 service_shutdown，启动时收敛旧 running 为 service_restarted，不自动重跑。只支持单实例 Web/会话池连接，`/api/ready` 包含内置执行器就绪。Checkpoint 清理精确限定 task-run:<UUID>。详见[定时任务设计](design-docs/task-center.md)与[验收](design-docs/task-center-acceptance.md)。

任务报告复用前端 ChatView 的普通 MessageBubble，任务运行元数据仅用于后台同步和普通聊天流隔离。删除专用 TaskRunMessage、结果引用草稿/接口/请求参数及相应分支；普通消息的头像、正文、复制与失败反馈共用，排队/执行中的后台消息依据实际元数据呈现生成状态，避免误判为断线。定时任务仍保留配置与运行管理。全局通知铃铛、弹出列表、通知轮询及自定义事件导航已移除，定时任务的任务名称通过直接回调打开固定会话，TaskRunsDrawer 由最近一次运行打开，保留历史选择、快照、失败诊断和当次取消；TaskDetail 及独立详情布局已删除，列表/抽屉共享 presentation.tsx，关闭不重置列表筛选/页码；随后可靠性收尾移除后端通知 API、已读字段与索引，通过 20261015_task_reliability 保留数据升级。


任务运行总时限从模型解析/历史准备开始计算；历史准备独立默认 60 秒/4 次摘要，后台摘要计入共享调用预算，追问摘要单独有界并传播取消。UTF-8 字节切块先验证总块数，超限不请求模型、不截断资料。进度只在正文变化时保存，终态与聊天消息同事务；状态只读事务保留执行连接互斥/PID 校验而不取任务全局排他锁。解析后模型展示名与消息原子同步，默认标题随任务名更新，自定义标题保留。

操作完整回执默认保留 30 天，过期请求显式拒绝；保留数据升级允许 response 为空并增加清理索引。周期清理每轮至多 100 条回执，只清空响应，键/请求摘要永久保留以阻止旧请求重复执行；拒重标记行数仍增长，不宣称账本总行数有界。新库直接创建当前 schema，已有库停 Web 后显式 db-update。


## 可选 Langfuse 追踪

配置统一经 `core/config.py`，客户端、运行 ContextVar 和回调配置经 `core/tracing.py`；隐私策略经 `core/trace_privacy.py`，OTel 状态/事件/链接经 `core/trace_exporter.py` 最终处理，`core/trace_callbacks.py` 只调整已知子 Agent 的观测名与安全错误，不重写官方层级/Token 解析。`ChatRuntime` 拥有单一客户端，Web 生命周期把它注入内置 TaskScheduler；停服先结束任务和聊天，再在线程中 shutdown 客户端及独立 OTel provider。

`services/tracing.py` 包裹真实聊天执行（回放不新建 trace），设置有意义的根输入/最终输出及中断状态；执行服务仅在调用图时合并监控回调，原 config、Agent 缓存、Checkpoint 与 SSE 协议不承载监控对象。后台从有效身份/数据库模型解析后包裹历史准备和执行；历史摘要使用同一运行回调。每次运行新建 handler，以 run_id 派生 trace ID，以租户和固定会话构造 session，审批恢复产生新 trace 并归入同一 session。

仅 Langfuse SDK 的 spans 发往 Langfuse，独立 provider 避免影响其他观测集成；创建阶段去除运行对象与常见秘密，导出阶段脱敏最终属性并执行内容开关。SDK 自动媒体上传强制关闭，Langfuse 凭据加入 Agent Shell 禁传清单。监控没有新增工具、HITL、API 或业务表，不改变 LocalShellBackend 的非沙箱限制。完整流程、限制和验证见[Langfuse 设计](design-docs/langfuse-tracing.md)。

## DeepSeek 联网工具

`tool/deepseek.py` 统一搜索/抓取的严格响应协议、同步异步传输、进程内按接口节流与有限重试；`search.py` 和 `task_search.py` 分别负责聊天和后台输出与权限边界。凭据只经 `core/config.py` 注入，工具不读取环境变量；异常结构不当作无结果，截断显式标记。后台不开放 Fetch。设计与并发/取消限制见[联网工具](design-docs/deepseek-web-tools.md)。

`POST /api/speech/transcribe` 只做语音转写代理：浏览器上传整段 WAV，`services/speech.py` 用同一把 `DEEPSEEK_API_KEY` 转发到 DeepSeek ASR，不进入 Agent、审批或代码解释器。音频不落库。见[聊天语音输入](design-docs/speech-input.md)。

`POST /api/speech/synthesize` 只做朗读代理：页面提交当前用户可见会话中的一条助手消息，服务端只读取该消息已保存的正文，用同一把 `DEEPSEEK_API_KEY` 向 DeepSeek 语音合成换取 PCM 流。这个入口停在 `api/routes/speech.py` 和 `services/speech.py`，不进入 Agent、审批或代码解释器，也不新增表或环境变量。见[朗读助手回复](design-docs/speech-read-aloud.md)。

语音路由使用 `api/auth.py` 的 JWT 和来源校验，并将身份共享锁限制在身份与会话归属检查。ASR 在鉴权后按实际收到的字节限制 multipart 上传。两个接口等待 DeepSeek 时均已释放身份共享锁。


### 环境变量与准备阶段（2026-10-18）

`services/environment.py` 为环境资源的权限与合并入口，`repository/environment.py` 管理持久化及原子审批；业务值只由数据库解析后显式传入 Backend，不修改进程环境。管理列表在服务层生成 masked_value 脱敏预览，仅超过 8 字符显示前后各 4 字符，短值全部遮盖，完整值不返回浏览器。`services/environment_chat.py` 在消息持久化／模型／监控之前提取敏感值，工具只携带消息绑定的引用；聊天确认进入 HITL，不进入子 Agent/PTC。两张新增表通过 `20261017_environment` 保留数据升级。系统基础环境和禁止注入规则集中在 `core/defaults.py`，旧默认凭据名单与部署白名单透传已删除。

`repository/resource_versions.py` 为显式资源写入口提供单进程提交—发布协议；共享全局版本，个人用户版本。取消保护事务完成并同步发布，发布失败阻止缓存读取。runtime 六因子键为工作区、租户、模型选择、能力、全局版本、用户版本；预热命中不进行资源检查 IO，未命中才解析模型和资源。构建前后核对版本、有界重试，保留 LRU、构建锁；仅存在 MCP 临时连接失败时不缓存 Agent，确定性配置错误仍允许缓存。未命中时经 `services/mcp_tool_cache.py` 跨会话复用相同配置与全局版本的 MCP 工具发现结果（成功 10 分钟、临时失败 30 秒，同键单次发现），并在 `api/lifespan.py` 启动后后台预热共享 MCP。QuickJS 解释器通过 `LazyCodeInterpreterMiddleware` 按需启动，未调用 `eval` 不写入堆快照；不可变 Skill 快照在实例内缓存元数据。仅单进程，离线 SQL／文件变更须重启。

API 先验签 JWT，再读取当前 active 用户及租户状态。业务请求在共享账户锁内拒绝停用租户，通过后复用该身份上下文。`services/preparation.py` 一次会话／项目归属联查后，在同次新消息、审批／问答准备与过期收尾复用上下文，不跨请求缓存。保留锁、幂等检查及副作用事务校验；两次主要身份／归属读取不代表整个消息只需两次 IO。准备计时覆盖认证依赖。范围、运行步骤与验收见[环境变量与聊天准备](design-docs/environment-and-preparation.md)。

SSE 事件拉取与源迭代器关闭显式复用每条流独立的 Context，避免心跳拉取任务切换导致 Langfuse ContextVar token 跨上下文复位；调用方不继承流内监控状态。工具发现的“下一次模型调用”发生于同一用户请求内，MCP 清单仅描述 MCP，不代表内置联网工具是否可用。

普通聊天的 `internet_search` 与 `web_fetch` 在 Agent 组装入口排除出动态候选目录，因此始终可见且不占会话工具池名额；主/子 Agent 共用规则，主 Agent 的 PTC 保留原只读白名单。后台搜索仍受授权控制，不开放 Fetch。


### 知识库当前契约

知识库支持资料导入、向量/关键词/混合检索及聊天引用；普通聊天固定混合，管理页保留单路对照。嵌入由根 `.env` 固定；OCR 使用 `DEEPSEEK_API_KEY`，地址写在 `core/knowledge_ocr.py`。同库一次处理一份资料，其他库可以并行。本轮选库后三个只读查询工具直接可用。

### 知识库引用与流程收敛（2026-11-10）

完整来源由内置只读工具生成；`output/knowledge_sources.py` 在展示预览截断前，从完整工具结果提取、校验受控版本 URL 并脱敏。根 Agent 的 `assistant_tool_result.result.knowledge_sources` 随 `assistant_steps` 保存、回放；子 Agent 的 `subagent_tool_result.knowledge_sources` 随已有展示事件保存。前端使用这些结构化字段构建白名单，不从预览正文扫描链接。来源元数据仅含标签、状态及版本化 URL，不包含原文、模型凭据或任意工具载荷；打开引用仍重新校验库权限。无 schema 变更，旧历史不回填，也不增加旧预览解析分支。

账户变更取消作业时按实际受影响的库统一检查流程释放，包括用户在共享库发起的作业；继续用 `execution_exited` 保证实际退出前不解锁。文档列表使用按文档关联的两次有界最新作业查询，复用既有文档/时间/ID 索引，不将全部作业历史搬到应用内筛选。当前边界见[执行、文件与恢复](design-docs/knowledge-base.md#执行文件与恢复)，历史验证见[审查修复记录](exec-plans/completed/2026-11-10-knowledge-review-fixes.md)。

### 知识库数据库结构（2026-11-06）

初始化 schema 直接包含知识库八张表和 `knowledge_search_chunks` 普通视图，不提供早期开发结构的保留数据升级。字段与约束见 [知识库设计](design-docs/knowledge-base.md)。知识库管理与模型用途配置已接入现有仓储、服务、Cookie API 和前端；PDF、Word、PowerPoint、图片、TXT/Markdown 导入、固定 OCR、分块嵌入与原子发布已实现。知识库嵌入由开发固定工厂提供，OCR 读取开发侧 .env，不读取用户目录默认，也不保存解析模型引用。初始化不创建默认模型或凭据。检索与普通聊天引用已实现，边界见下文。

先安装 PostgreSQL 的 pgvector 扩展包（见 [部署说明](../ops/PGVECTOR.md)）；扩展尚未启用时脚本执行 `CREATE EXTENSION IF NOT EXISTS vector`，若数据库账号无权限，需数据库管理员预先安装扩展。固定向量维度 1024。空库执行 `uv run melonclaw-db-init`。已有业务库执行 `uv run melonclaw-db-update`，步骤 `20261117_knowledge` 创建当前八张表和检索视图，不改已有业务表。`20261126_knowledge_moderation` 只扩展知识库事件操作类型。已有知识库表与当前结构不一致时升级失败并回滚。命令本身不清库，服务启动不会自动建表或升级。system/admin 可按整库停用、恢复或逻辑删除；通用可见性查询仍只返回使用中的库。操作记录按库分页读取已有事件，不返回正文。审计文件写入失败时，若配置了接收地址，则在进程内告警，告警本身不写审计文件。


知识库管理：`api/routes/knowledge.py → services/knowledge.py → repository/knowledge.py` 复用 BusinessRepository，不新增包或资源表。core/model_catalog.py 保留模型用途隔离；知识库建库不解析用户目录中的文档解析默认，`parser_model_id` 保留为可空外键且保持为空。知识库模型由开发维护，固定 OCR 与导入链路见[文档导入](design-docs/knowledge-base.md#文档导入)。建库绑定与模型/供应商更新通过事务级 catalog 锁串行化，资源更新沿原 resource_change 提交—发布边界失效。账户换租户保留知识库点名授权。删除用户或停用租户仍通过 account_guard 和用户→库→文档→版本→job 锁顺序取消相关作业；删除用户同时移除其成员授权。知识库事件同事务提交。知识库成员角色为查看者和编辑者。库设置与成员管理归所有者及系统管理员，转让后原所有者成为编辑者。共享范围记在 `knowledge_bases.share_scope`。知识库不开放嵌入或解析模型连接测试；聊天模型连接测试仍走既有模型管理入口。见[管理实现与边界](design-docs/knowledge-base.md#授权与账户变化)。


### 开发侧固定嵌入入口

`core/knowledge_embedding.py` 是唯一嵌入实例工厂，模型、端点、维度与 Key 均取自 config 的环境配置副本；float 编码、16 条批次、60 秒超时和零重试保持不变，显式构造 trust_env=False 的同步/异步 HTTP 客户端。`core/config.py` 仅从 KNOWLEDGE_EMBEDDING_API_KEY 取得开发凭据；历史独立 demo 已删除，运行与验证均使用包内实现。聊天仍走 model_catalog；知识库解析不读取用户目录默认，固定 OCR 由 core/knowledge_ocr.py 提供；固定嵌入不读取数据库或访问者 Key。

`repository/knowledge_embedding.py` 首次建库时登记稳定 UUID 的系统引用，保留八表和 embedding_model_id 外键。登记经原 resource_change 提交—发布边界，重复建库仅验证复用，不写配置或推进资源版本；语义冲突明确报错，不覆盖记录。模型/供应商管理入口隐藏并拒绝嵌入配置（包括 admin），聊天模型仍按 purpose=chat 隔离。建库 API 不接受 embedding_model_id，响应也不返回该内部字段。后续向量处理必须检查固定引用，不能忽略历史库绑定。这一段只说明固定嵌入引用本身不新增表；导入执行器见后文。

嵌入四项部署值统一由 config 从根 .env 读取：KNOWLEDGE_EMBEDDING_API_KEY、KNOWLEDGE_EMBEDDING_URL、KNOWLEDGE_EMBEDDING_MODEL_NAME、KNOWLEDGE_EMBEDDING_DIMENSIONS；工厂与内部引用通过显式 settings 参数复用同一副本，无具体配置值默认回退。向量列维度保留结构校验，具体模型名只要求非空；不自动转换既有引用或向量空间。

### 知识库导入执行边界

`api/routes/knowledge.py → services/knowledge_documents.py → repository/knowledge_documents.py` 登记受控原件和幂等作业；`services/knowledge_executor.py` 在 Web 生命周期持有单实例数据库会话锁，按数据库队列领取。同一个库和同一份文档不并行；同一个人的其他库可以同时处理。导入受全局并发上限约束。`services/knowledge_processing.py` 协调无凭据的独立解析进程、固定 OCR、LangChain 分块与嵌入；`storage/knowledge.py` 管理 UUID 路径，`parsers/knowledge_worker.py` 不导入应用配置。OCR 入口 `core/knowledge_ocr.py` 只消费 Settings，无 SDK 或目录配置回退。所有部署值从 core/config.py 读取，不读取 demo 环境。向量由 repository 参数化 CAST 写入八表，正文/chunks/有效指针/job/事件同事务发布；网络调用不持事务锁。锁连接失效取消本地任务，不重连；取消后协程和子进程实际退出前不释放占用。失败或未发布版本不暴露为可用版本。详见[当前导入契约](design-docs/knowledge-base.md#文档导入)。

### 知识库检索与聊天边界

查询入口为 `api/routes/knowledge.py → services/knowledge_query.py`，由 knowledge_search 查询原文候选。服务使用显式异步查询编排和统一固定向量工厂，不缓存身份/检索结果；SQL在有效版本视图上强制租户、库、嵌入空间与当前ACL，网络后及返回前重验。混合模式并行调用两路独立仓储事务，按 chunk_id 以 RRF(k=60) 融合排名，每路与融合候选均最多60条；向量阈值不作用于RRF。分支20秒、整体45秒，只有向量服务故障/超时可显式降级到关键词，权限、配置、版本和关键词执行失败不降级。普通聊天 `tool/knowledge.py` 仅依赖 Protocol 与 core 输入/异常，provider由 ChatService 注入 ChatRuntime，再传 core/agent 装配；身份、每轮显式库选择和每库查询计数字典通过 AgentContext，不能闭包绑定到缓存Agent。每库允许3次初始/子问题查询与2次补查，多库独立；恢复执行使用新的有界计数，不重放旧工具。执行准备把选择ID写入用户消息metadata并纳入幂等校验，恢复沿用本轮选择，权限不快照放行。本轮已经选择知识库时，普通主/子 Agent 直接看到三个只读查询工具；未选择时这些工具留在动态目录，不能代替输入区选择。工具不进入 PTC 或无人值守任务。搜索工具不暴露mode；打开原文可按原chunk_ids读取同revision的合并上下文，单区间8000、单次合计16000字符，引用按实际范围生成。来源链接由服务生成结构化ID，UI只展示受控纯文本。详情见[混合检索与按需补证](design-docs/knowledge-base.md#混合检索与按需补证)。


### 知识库整文件 OCR 与本地文本读取

知识库导入白名单独立于聊天附件。TXT 与 Markdown 由无网络的 `parsers/knowledge_worker.py` 本地读取。PDF、Word（DOC、DOCX）、PowerPoint（PPT、PPTX）和 PNG、JPEG、WebP、GIF、BMP、TIFF 由 `core/knowledge_ocr.py` 整份提交到 `https://api.deepseek.com/v1/tools/ocr`。凭据使用 `Settings.deepseek_api_key`，超时由 `KNOWLEDGE_OCR_TIMEOUT_SECONDS` 读入 Settings。导入配方不保存 OCR 地址或 Key。图片的 `purpose` 为 `rich_layout_recognition`，文档为 `text_recognition`。索引用剥离版面标记后的正文，`source_locator` 只记录页码和 `method=ocr`。不保留本地 Office 结构定位，也不接受 XLSX。原件会发往 DeepSeek，按成功页计费；客户端超时后远端仍可能完成并计费。详见[现行导入契约](design-docs/knowledge-base.md#文档导入)。无新横切入口、工具或数据库结构。
