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
Project 或普通 Conversation 的受控 `.attachments/` 目录，`services/attachments.py` 完成校验和后台解析；
发送消息时只提交附件 ID，由 `repository/attachments.py` 在消息事务中绑定。文档由
`parsers/` 生成派生 Markdown，图片由注入式 hydration middleware 在模型出站前转换为
标准 image content block；出站图片经 `services/attachment_images.py` 按文件版本缓存并
等比缩放，已失效的附件在 hydration 阶段降级为提示而不是让整轮执行失败。

消息准备先检查幂等键并取得会话锁，再构建新运行的 Agent；已结束请求直接回放
业务消息快照。无论有无附件，user/assistant 消息对都由同一事务写入。v3 事件
投影器与执行服务共用一份 assistant steps 快照，用于 SSE 增量与最终落库；
会话锁仍持有到流结束，以保护同一 Checkpoint 的单执行语义。

持久化分两块，职责不重叠：

- `database/` + `repository/`：业务数据（用户、Project、Conversation、消息、审批、用户问题交互、Memory 事件），由 `melonclaw-db-init` 建表。
- LangGraph Checkpointer：Agent 图状态，与业务表分离。

`tenants` 与 `users` 是一对多关系：`users.tenant_id` 是非空外键，租户角色和状态也直接保存在用户行中，不设成员关联表。请求只提供开发模拟 `user_id`；服务层读取该用户唯一且有效的租户归属，生成 Agent 和 Memory 的运行上下文。Tenant Memory 按这个租户 ID 分区并按用户租户角色授权；Memory 操作会再次核对运行上下文与数据库归属。用户租户归属没有运行时变更入口，调整开发数据时重建数据库。

**身份决定租户隔离的实际强度。** 当前浏览器可提交任意 `user_id`，它只是开发模拟身份，不能抵御冒用其他用户。`api/identity.py` 的受信任身份头目前仅接入附件路由；面向共享环境时，必须先让所有受保护路由统一使用可信认证，并隔离 Agent 的文件与 Shell 能力。任何客户端请求、API Schema、查询或表单参数都不得定义或消费 `tenant_id`；租户 ID 只能从服务端读取的用户记录进入运行上下文。

Conversation 的主键是 `chat_conversations.id`，表内没有重复的 `conversation_id` 列；
消息、审批、用户问题和附件关系表使用 `conversation_id` 外键引用它。Conversation 归属由
`user_id + project_id` 确定：`project_id IS NULL` 表示普通会话，工作区为
`conversations/<conversation_id>`；非空时工作区为项目的 `workdir_path`，同一项目内的会话共享文件，
但仍各自使用独立的消息序列和 Checkpoint。项目和普通会话的附件分别由
`chat_attachments.project_id` 与 `owner_conversation_id` 表示，数据库 CHECK 保证恰有一个归属。
项目与会话各自持久化 `is_pinned` 供侧栏排序；删除使用 `status=deleted` 逻辑删除，读取时过滤已删除项目及其会话，保留跨数据库、Checkpoint 和文件系统的原始数据以避免非原子清理。
普通会话加入项目时保留会话 ID、消息和 Checkpoint；先把该会话独享工作区里的普通文件、artifacts 和已登记附件复制到目标项目工作区，再在事务中把 Conversation 与附件归属切为项目。移动成功后清理原目录，后续运行只走项目工作区。项目内会话不支持再次移动；目标同名文件、运行中的会话和未提交附件会阻止移动。无需新增表列或历史数据兼容读取分支。

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
| `middleware/` | Agent 中间件：文件操作顺序、工具动态选择、用户提问批次护栏 | `file_ordering.py`、`tool_selection.py`、`user_input_guard.py` |
| `backend/` | Deep Agents Backend 的构造与路径路由 | `factory.py` |
| `tool/` | 注入 Agent 的工具（联网搜索、MCP 目录、受控 Skill 安装） | `tools.py`、`search.py` |

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
| `main_web.py`、`main_db_init.py`（入口） | 任意包 | 无 |

补充规则（同样由测试强制）：**`api/` 只能被入口文件 `main_web.py` 导入**，业务包不得反向引用 Web 层。

### 为什么 `core/` 不是“最底层”

`core/` 里有两类内容，不要混为一谈：

- **基础件**：`config.py`、`defaults.py`、`model_catalog.py`、`chat_model.py`、`mcp_config.py` —— 只被别人依赖，不依赖业务包。
- **组装点**：`agent.py` 是 Agent 的装配处，会主动引用 `backend/`、`memory/`、`middleware/`、`tool/`；`hitl.py` 会引用 `output/formatting.py` 做脱敏。

所以本仓库不采用 `core → repository → services → api` 这种完全单链表，而是逐包声明禁止边。新增包时，先在 `tests/test_architecture.py` 的 `FORBIDDEN_IMPORTS` 里补一行，再写代码。

## 4. 横切关注点只从固定入口进入

| 关注点 | 唯一入口 | 说明 |
|---|---|---|
| 配置与环境变量 | `core/config.py` 的 `Settings` / `load_settings()` | 新增环境变量在这里解析，不要在各模块直接读 `os.environ`（`MELONCLAW_*` 风格） |
| 模型与能力 | `core/model_catalog.py` 的 `ResolvedModel` + 目录条目转换；模型目录以 `model_providers` + `model_configs` 两张表为唯一事实来源（供应商持有 base_url/api_key/模型列表端点，模型经 `provider_key` 引用供应商；db-init 只写无凭据、未启用的供应商模板，不创建模型），runtime 按 user_id 查可见行并联查供应商行 | 请求只能提交目录模型 ID（`custom:` 前缀），不接受前端传任意模型名；`model_providers.api_key` 只写不回读，模型停用或缺少有效 Key 时不可用，全局模型另受供应商开关控制；`ResolvedModel.cache_key` 同时携带模型版本与供应商版本，供应商换 Key 后旧 Agent 缓存即时失效；DB 无可用行时返回空选择并提示配置；Agent 构建必须显式接收数据库解析后的模型 |
| 模型实例 | `core/chat_model.py` 的 `build_chat_model()` | 所有 provider（含 custom）统一经此构造 |
| MCP 服务定义 | `services/mcp.py` 的运行时装配（数据来自 `mcp_servers` 表）；根目录 `mcp.json` 只是 db-init 的内置种子 | 无 MCP 时必须能正常启动；user scope 仅 http/sse 且禁止 `${VAR}`，global scope 允许 stdio 与 `${VAR}`（管理员发布） |
| 长期记忆 | `memory/` 的 `MemoryService` | 写入必须经过它的固定工具与审计，不直接写 Store |
| HITL 与副作用工具 | `core/hitl.py` 的审批清单 + `FilesystemPermission`；用户问题由 `core/user_input.py` 的 `interrupt()` 进入同一 Checkpoint | 写文件、删文件、Shell 等有副作用的操作必须走审批或权限边界；缺少关键用户决策时 Agent 可暂停等待回答；审批恢复必须绑定当前 `approval_batch_id` 与 `assistant_message_id`；用户问题的唯一出口是一次真正的 `Command(resume=...)`，答案可以是选项/文本，也可以是 `{"type": "cancelled"}`（用户跳过或 TTL 过期后由 `services/user_input_execution.py` 代答），只改 `user_interactions` 状态不会解除 Checkpoint 挂起；答案已收但本轮没跑完时账本会被标成 `recovery_required` 并且**禁止自动重放**（不知道副作用执行到哪一步），此时历史表现为失败，唯一的解锁入口是用户发新消息——那时服务层会先代答取消、把 Checkpoint 叫醒收尾 |
| 业务数据持久化 | `repository/` 的 `BusinessRepository` | `services/` 不直接写 SQL |
| Skill 资源 | `services/skills.py` 解析正文；`skill_state.py` 统一有效状态；`skill_import.py` 导入更新；`skill_operations.py` 文件日志恢复；`skill_index.py` 索引重建 | 正文在文件系统，DB 保存归属、启停、版本和来源。持久草稿与内容提交受同数据根文件锁保护；更新保留 ID/偏好，删除先隔离目录。管理、Picker 和 Agent 共用有效状态；`skill_snapshot.py` 固定有效目录内容与摘要，`middleware/skill_refresh.py` 每轮刷新 Checkpoint 摘要。详见 [Skill 生命周期](design-docs/skill-lifecycle.md)。 |
| Skill/模型资源管理 | `services/resource_service.py` | 权限矩阵在这里强制执行：global 资源仅 admin/owner 可管理，user 资源仅创建者（含 admin）可动 |
| 附件请求身份 | `api/identity.py` 的 `resolve_request_user_id()` | 附件路由决定 `user_id` 从哪里读（查询/表单，或部署方配置的受信任请求头）；**不做身份校验**，用户的唯一租户归属和资源所有权仍由服务层重新校验 |

凭据相关有一条硬规则：`DEEPSEEK_API_KEY`、`DATABASE_URL`、`TAVILY_API_KEY` 等应用自身凭据**不能**注入给 Agent 执行的命令；给 Agent 的变量名集中在 `core/defaults.py` 声明，值只来自 `.env`。

### 聊天安装 Skill 与教程

聊天生成入口 `prepare_skill_creation` 与安装工具共用 provider 和确认审批。`services/skill_creation.py` 校验至多 32 文件、64000 UTF-8 字节的 SKILL.md/文本参考并打包；`services/skill_install.py` 绑定身份、会话和 generated 来源，复用 SkillImportService 与 SkillOperations。内置 skill-creator 负责内容提炼，服务端按 admin/owner global、member user 决定目录与数据库归属，不增加 HTTP 回环或数据写入旁路。详见 [聊天生成设计](design-docs/chat-skill-creation.md)。

`ChatService` 向 runtime 注入 `ChatSkillInstallService` provider，`core/agent.py` 使用 `tool/skill_install.py` 的协议创建准备和确认工具。身份由 `ToolRuntime` 的 `AgentContext.user_id/tenant_id/conversation_id/project_id` 提供，每次执行重新查询用户、会话与项目；模型不提供身份。聊天与页面共用导入服务，confirm 通过 `core/hitl.py` 审批，按草稿核对完整清单及会话，不进入 PTC。ZIP 附件使用 archive 类型，仓储绑定时无需文档解析或视觉模型，hydration 仅传附件 ID。安装并启用在原操作日志 ready 提交中完成，下一轮快照生效。教程通过现有内置索引默认启用。详见 [设计与边界](design-docs/chat-skill-install.md)。

## 5. 关键取舍与历史教训

### 5.1 `LocalShellBackend` 不是安全沙箱

默认后端允许本地文件和 Shell 能力，只适合本机开发。面向共享或不受信任用户部署前，必须替换为受控 SandboxBackend 或独立解析服务。这一条在 README 的“使用边界”里也对用户可见。

Deep Agents 0.7 对带 `execute` 能力的 backend 不支持默认路径上的
`FilesystemPermission`。因此 `/.attachments/` 和 `/.artifacts/` 被路由到不提供
Shell 执行的 `FilesystemBackend`，附件原文、派生目录和运行时 artifacts 的工具权限
可以继续生效；`execute` 始终只委托给默认工作区 backend，并继续由 HITL 审批。这个
路由不能把本机 Shell 变成沙箱，生产部署仍需替换默认 backend。

### 5.2 不要依赖上游框架按类型推断能力

DeepSeek / MiniMax 都通过 `ChatOpenAI` 适配。`deepagents` 会把 `ChatOpenAI` 这一类**按类名**判定为“能接受非 PDF 文件块”，从而绕过按模型能力（`ModelProfile`）做的门控；而 `ChatOpenAI` 的能力表只收录 `gpt-*` 系列，我们的模型名拿不到能力声明，所有门控默认放行。

结论：**涉及模型能力的判断，必须由本仓库显式声明，不能依赖框架自动推导。** 这条经验来自聊天附件功能的调研，详细证据见 `note/上传附件技术设计.md` 的 §3.2、§3.3。

### 5.3 内容块转换是框架职责，路由是应用职责

标准内容块到各家 provider 请求体的转换由 LangChain 提供；“该不该发这种块”和“被拒之后怎么降级”属于应用层职责。第三方非标准推理协议是例外：`core/reasoning.py` 解析供应商 `extra_config._melonclaw.reasoning_format`，`core/chat_model.py` 同时适配响应增量、完整消息和下一次请求。原始推理字段保存在 Checkpoint，展示只使用脱敏限长的有序内容块；签名、加密片段不进入 UI，也不从展示快照重建模型历史。

### 5.3.1 回复流畅性与会话内工具选择

`core/tool_catalog.py` 定义轮次 ID、目录摘要和 Checkpoint 状态；`middleware/tool_selection.py` 每用户轮次初选一次，审批和提问恢复复用。共享 Project Agent 不保存会话选择。目录摘要覆盖名称、描述和输入 schema；变更会使缓存失效。`tool/tool_discovery.py` 的 `find_tools` 仅检索组装时已授权目录，用 Command 激活后续可见 schema；轮次上限由 reducer 共同约束并行发现结果，不扩大 MCP 白名单、HITL 或 PTC。选择超时配置仍从 `core/config.py` 进入。

`output/events.py` 并发排空各 ToolCallStream，快工具结果不等待慢工具。生成调用仅标记 queued；观测工具执行才记开始时间，耗时用单调时钟。`services/execution_trace.py` 保存阶段耗时及子 Agent 展示轨迹；准备阶段仍在 SSE 之前，错误保持 HTTP 语义。`output/stream_redaction.py` 在连续增量之间识别凭据，普通正文不作整段缓冲。SSE 与最终 `assistant_steps.content_blocks` 同时保留正文/推理顺序；细节见 [回复流畅性与推理协议](design-docs/response-fluency.md)。

### 5.4 消息长度的双重约束

- 业务侧：`MessageRequest.content` 限制 1–12000 字符。
- 框架侧：`read_file` 等工具结果有 token 阈值，超限会被截断或改成文件引用。两端都要考虑，不要只按其中一侧设计。

### 5.5 错误响应的脱敏边界

`api/errors.py` 的 `error_response` 会对异常文本调用 `output/formatting.py` 的 `sanitize_text`，但它只脱敏环境变量、Token 和授权字段的值，**不管文件系统路径**。而 `shutil`、`open()`、`os` 抛出的原生异常恰好都带完整绝对路径。所以任何会被交给 `error_response` 的异常都不得携带宿主机路径：涉及文件的写操作要在调用前显式检查并抛业务异常（如下载 Skill 前先确认源目录存在），不要让原生异常冒到 API 边界。同理，用户能自己纠正的失败（如已选技能在执行前被移除）应通过异常上的 `status_code` 映射成 4xx，而不是落到默认的 500 让前端当成服务端故障重试。

## 6. 数据库变更流程

数据库按“可清空重建”维护，`database/schema.py` 是唯一事实来源，不为历史数据写兼容迁移。

1. 改 `database/schema.py` 的表定义（列、约束、部分唯一索引都写在这里）；
2. 清空/重建数据库后执行 `uv run melonclaw-db-init`：`create_schema` 只做一次 `metadata.create_all`，`seed_demo_data` 写入演示数据，`reindex_skills_from_disk()` 从 `data_root/skills/` 补回 Skill 索引；
3. 服务启动时 `verify_schema` 只做校验，**不会**自动迁移，也不会补列。

清库会丢掉两类"看起来在代码里、实际只在数据库里"的东西，`db-init` 对它们的处理不同：**结构**必须由 `schema.py` 定义（当前结构校验由 `verify_schema` 执行），**索引类数据**由 `db-init` 从磁盘或仓库种子重建（内置 MCP 来自 `mcp.json`，Skill 索引来自 `data_root/skills/`）。新增任何"DB 只是投影"的资源时，都要同时给出重建入口，否则清库后它会静默消失而不是报错。

`metadata.create_all` 只建缺失的表，从不 ALTER 已有表。`verify_schema` 对 `BUSINESS_TABLES` 逐表比对当前定义的列；数据库结构变更通过清空开发库并重新初始化完成。**业务表和资源表都必须登记在 `database/constants.py` 的 `BUSINESS_TABLES`**：漏登记的表不会做列校验，漂移可能到种子 INSERT 才以数据库错误暴露。新增业务表时，同时把它加入 `BUSINESS_TABLES`。

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
缓存摘要来自同一配置/偏好快照，涵盖删除；失败工具发现不永久缓存。
MCP 工具通过 `core/hitl.py` 动态注册审批，不进入 PTC；命名空间中的连接指纹防止旧审批
调用新连接。JSON 原文只在前端导入，不进入数据库或模型。
工具白名单选择统一使用 `core/mcp_config.py` 的 `select_mcp_tool_names`，按目录交集提供工具，
缺失项警告不扩大授权。管理发现由 `ChatRuntime.mcp_discovery`（`services/mcp_discovery.py`）
调度，权限/版本校验先于共享任务与脱敏目录缓存；草稿和 stdio 不缓存，Agent 独立发现并保持审批。
完整权限、API、审批恢复边界与验证见 [MCP 两层设计](design-docs/mcp-two-layer.md)。

### 聊天安装 MCP

消息准备阶段由 `services/mcp_chat_config.py` 提取 MCP JSON，数据库 `mcp_install_drafts` 保存受控配置，消息、Checkpoint 和模型只接收草稿引用。`ChatService` 注入 `ChatMcpInstallService` provider，`tool/mcp_install.py` 提供准备、审批测试和审批安装工具。身份从 ToolRuntime 注入并重新校验用户、会话和项目，不允许模型指定用户。聊天安装固定个人范围，`McpManagementService.prepare(personal=True)` 复用管理校验；资源页原有角色默认范围保持不变。

正式安装由 `repository/mcp_install.py` 在同一事务写配置、个人偏好和草稿结果，行锁保护并发幂等；不自动覆盖同名个人项。测试和安装进入 HITL，均不进入 PTC，下一轮沿用现有 MCP 快照摘要生效。草稿有效期 24 小时，过期访问拒绝，暂存新配置时清理；审批草稿安装成功后清空 payload。详见 [聊天安装设计](design-docs/chat-mcp-install.md)。


### 聊天结果组件与成果读取

结构化结果使用原有 assistant 正文中的 `melon-result` JSON 围栏，不另开 SSE 或持久化旁路。提示词走 `core/prompts.py`；前端只接收固定类型、有限大小的数据，使用固定组件。结果格式校验在浏览器完成，文件元信息和读取权限由服务端核验，不能把格式校验等同于数据事实核验。

`services/results.py` 通过 `ConversationService` 和 `repository/` 解析有效身份、会话及项目归属，通过 `ChatRuntime.workspace_dir` 定位现有工作区；`storage/results.py` 只读 `/outputs/` 交付文件，目录 fd 与 NOFOLLOW 避免符号链接替换。HTTP 内容默认下载、禁止嗅探、sandbox CSP；预览类型由实际内容检查决定。没有新增 Agent 工具，生成文件继续经过原有 HITL；LocalShellBackend 的非沙箱边界仍有效。详见 [聊天结果组件](design-docs/chat-result-components.md)。

`services/result_index.py` 从已完成最终回复的 Markdown 语法提取明确文件／图片交付，history 和 completed（含幂等回放）使用同一派生 `artifacts` 字段；文件卡片、正文链接与会话产物列表共用引用。`ResultFileService.index` 经 repository 直接读取 `conversation_artifacts` 交付投影，按路径／附件 ID 合并最近来源，不加载消息正文或扫描工作区。最终正文的引用由 services 解析，repository 在完成回复的 CAS 事务中同步写入投影；以消息 seq 阻止迟到旧交付覆盖新来源。`melonclaw-db-init` 从已完成正文流式、原子重建投影，读取路径不做历史回退。文件仍读取当前内容，不保存版本快照。HTML 使用独立只读预览响应，UTF-8／2 MB 核验、HTTP 与 Blob 文档 meta CSP、无同源权限 sandbox；普通 content 的 HTML 下载边界保持严格。具体流与限制见 [对话产物](design-docs/conversation-artifacts.md)。

文件浏览器复用 `ResultFileService` 的身份与实际工作区解析。`storage/workspace_files.py` 提供按目录读取、5,000 项扫描上限、分页和普通文件 fd 读取；拒绝隐藏路径、符号链接与特殊文件。`storage/results.py` 在同一读取机制上保留成果 `/outputs/` 引用约束。`api/routes/files.py` 的 `/files` 目录／内容接口不扩大 Agent 成果协议权限，内容与 HTML 策略复用既有响应构造。附件列表只经 `repository/attachments.py` 按实际项目／会话与用户查登记信息，不枚举内部物理目录。文件存在与完成回复的交付记录分别读取，不增加表或快照。详见 [文件浏览器](design-docs/workspace-file-browser.md)。
