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
backend/        CompositeBackend：默认 LocalShellBackend(当前工作区) + 受保护目录与 /skills/ 路由
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

Conversation 的主键是 `chat_conversations.id`，表内没有重复的 `conversation_id` 列；
消息、审批、用户问题和附件关系表使用 `conversation_id` 外键引用它。Conversation 归属由
`user_id + project_id` 确定：`project_id IS NULL` 表示普通会话，工作区为
`conversations/<conversation_id>`；非空时工作区为项目的 `workdir_path`，同一项目内的会话共享文件，
但仍各自使用独立的消息序列和 Checkpoint。项目和普通会话的附件分别由
`chat_attachments.project_id` 与 `owner_conversation_id` 表示，数据库 CHECK 保证恰有一个归属。
项目与会话各自持久化 `is_pinned` 供侧栏排序；删除使用 `status=deleted` 逻辑删除，读取时过滤已删除项目及其会话，保留跨数据库、Checkpoint 和文件系统的原始数据以避免非原子清理。
普通会话加入项目时保留会话 ID、消息和 Checkpoint；先把该会话独享工作区里的普通文件、artifacts 和已登记附件复制到目标项目工作区，再在事务中把 Conversation 与附件归属切为项目。移动成功后清理原目录，后续运行只走项目工作区。项目内会话不支持再次移动；目标同名文件、运行中的会话和未提交附件会阻止移动。无需新增表列或历史数据兼容读取分支。

## 2. 模块职责

| 包 | 职责 | 典型文件 |
|---|---|---|
| `core/` | 配置、模型目录、模型工厂、提示词、Agent 组装、HITL 审批与用户问题、PTC Interpreter、MCP 配置与脱敏 | `config.py`、`model_catalog.py`、`chat_model.py`、`agent.py`、`hitl.py`、`user_input.py` |
| `database/` | 连接、表结构定义、建表与完整性校验、常量 | `schema.py`、`migrations.py`、`constants.py` |
| `repository/` | 业务数据的读写、事务边界、会话锁、上下文与用户解析 | `repository.py`、`conversations.py`、`attachments.py`、`user_interactions.py`、`locks.py`、`bootstrap.py` |
| `parsers/` | 附件扩展名/MIME/容器安全校验，以及受控文档到 Markdown 派生文件的解析 | `validation.py`、`documents.py` |
| `storage/` | 当前工作区内附件原文、派生文件与临时文件的受控路径映射和发布 | `attachments.py` |
| `services/` | 用例编排：执行、执行收尾、用户问题恢复、会话、技能、运行时资源管理 | `execution.py`、`execution_finalize.py`、`user_input_execution.py`、`runtime.py`、`chat.py`、`skills.py` |
| `api/` | HTTP 边界：路由、Schema、错误映射、SSE 编码、应用生命周期 | `app.py`、`routes/*`（含 `user_input.py`）、`schemas.py`、`sse.py` |
| `output/` | 通过当前 Deep Agents v3 事件投影提取模型可见文本，并把根 Agent 的每次 AIMessage 投影成有序 assistant steps；子 Agent 保留任务卡事件 | `events.py`、`assistant_steps.py`、`visible_text.py`、`formatting.py` |
| `memory/` | Global / Tenant / User 三级长期记忆的中间件、工具与服务 | `service.py`、`middleware.py`、`tools.py` |
| `middleware/` | Agent 中间件：文件操作顺序、工具动态选择、用户提问批次护栏 | `file_ordering.py`、`tool_selection.py`、`user_input_guard.py` |
| `backend/` | Deep Agents Backend 的构造与路径路由 | `factory.py` |
| `tool/` | 注入 Agent 的工具（联网搜索、MCP 目录工具） | `tools.py`、`search.py` |

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
| 模型与能力 | `core/model_catalog.py` 的系统模型目录 + `ResolvedModel` | 请求只能提交系统模型 ID，不接受前端传任意模型名 |
| 模型实例 | `core/chat_model.py` 的 `build_chat_model()` | 所有 provider 统一经此构造 |
| MCP 服务定义 | `core/mcp_config.py` + 根目录 `mcp.json` | 无 MCP 时必须能正常启动 |
| 长期记忆 | `memory/` 的 `MemoryService` | 写入必须经过它的固定工具与审计，不直接写 Store |
| HITL 与副作用工具 | `core/hitl.py` 的审批清单 + `FilesystemPermission`；用户问题由 `core/user_input.py` 的 `interrupt()` 进入同一 Checkpoint | 写文件、删文件、Shell 等有副作用的操作必须走审批或权限边界；缺少关键用户决策时 Agent 可暂停等待回答；审批恢复必须绑定当前 `approval_batch_id` 与 `assistant_message_id`；用户问题的唯一出口是一次真正的 `Command(resume=...)`，答案可以是选项/文本，也可以是 `{"type": "cancelled"}`（用户跳过或 TTL 过期后由 `services/user_input_execution.py` 代答），只改 `user_interactions` 状态不会解除 Checkpoint 挂起；答案已收但本轮没跑完时账本会被标成 `recovery_required` 并且**禁止自动重放**（不知道副作用执行到哪一步），此时历史表现为失败，唯一的解锁入口是用户发新消息——那时服务层会先代答取消、把 Checkpoint 叫醒收尾 |
| 业务数据持久化 | `repository/` 的 `BusinessRepository` | `services/` 不直接写 SQL |
| 请求身份 | `api/identity.py` 的 `resolve_request_user_id()` | 决定 `user_id` 从哪里读（查询/表单，或部署方配置的受信任请求头）；**不做身份校验**，成员关系与归属仍由服务层重新校验 |

凭据相关有一条硬规则：`DEEPSEEK_API_KEY`、`DATABASE_URL`、`TAVILY_API_KEY` 等应用自身凭据**不能**注入给 Agent 执行的命令；给 Agent 的变量名集中在 `core/defaults.py` 声明，值只来自 `.env`。

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

标准内容块到各家 provider 请求体的转换由 LangChain 提供，不需自己实现；但“该不该发这种块”和“被拒之后怎么降级”属于应用层职责。

### 5.4 消息长度的双重约束

- 业务侧：`MessageRequest.content` 限制 1–12000 字符。
- 框架侧：`read_file` 等工具结果有 token 阈值，超限会被截断或改成文件引用。两端都要考虑，不要只按其中一侧设计。

## 6. 数据库变更流程

数据库按“可清空重建”维护，`database/schema.py` 是唯一事实来源，不为历史数据写兼容迁移。

1. 改 `database/schema.py` 的表定义（列、约束、部分唯一索引都写在这里）；
2. 清空/重建数据库后执行 `uv run melonclaw-db-init`：`create_schema` 只做一次 `metadata.create_all`，`seed_demo_data` 写入演示数据；
3. 服务启动时 `verify_schema` 只做校验，**不会**自动迁移，也不会补列。
