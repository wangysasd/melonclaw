# MelonClaw 架构

本文档描述代码的组织方式、依赖方向，以及几条不写在代码注释里的关键取舍。改动模块边界前请先读这里。

## 1. 顶层数据流

```
浏览器（frontend/，React + Vite）
        │  POST /api/conversations/{id}/messages  (JSON)
        ▼
api/            FastAPI 路由、请求校验、SSE 编码
        ▼
services/       归属校验、request_id 幂等、会话锁、消息落库、执行编排
        ▼
core/agent.py   用 create_deep_agent 组装 Agent（模型 + 工具 + middleware + backend）
        ▼
runtime.py      按 Project 解析工作区，按 (project_id, model) 缓存 Agent
        ▼
backend/        CompositeBackend：默认 LocalShellBackend(Project 工作区) + /skills/ 路由
        ▼
output/         把 LangGraph 消息流转成 SSE 事件（text / tool_call / approval 等）
```

持久化分两块，职责不重叠：

- `database/` + `repository/`：业务数据（用户、Project、Conversation、消息、审批、Memory 事件），由 `melonclaw-db-init` 建表。
- LangGraph Checkpointer：Agent 图状态，与业务表分离。

## 2. 模块职责

| 包 | 职责 | 典型文件 |
|---|---|---|
| `core/` | 配置、模型目录、模型工厂、提示词、Agent 组装、HITL 审批清单、PTC Interpreter、MCP 配置与脱敏 | `config.py`、`model_catalog.py`、`chat_model.py`、`agent.py`、`hitl.py` |
| `database/` | 连接、表结构定义、迁移与 schema 版本、常量 | `schema.py`、`migrations.py`、`constants.py` |
| `repository/` | 业务数据的读写、事务边界、会话锁、上下文与用户解析 | `repository.py`、`conversations.py`、`locks.py`、`bootstrap.py` |
| `services/` | 用例编排：执行、会话、技能、运行时资源管理 | `execution.py`、`runtime.py`、`chat.py`、`skills.py` |
| `api/` | HTTP 边界：路由、Schema、错误映射、SSE 编码、应用生命周期 | `app.py`、`routes/*`、`schemas.py`、`sse.py` |
| `output/` | 从 LangGraph 消息/事件里提取模型可见文本与前端展示事件 | `events.py`、`visible_text.py`、`formatting.py` |
| `memory/` | Global / Tenant / User 三级长期记忆的中间件、工具与服务 | `service.py`、`middleware.py`、`tools.py` |
| `middleware/` | Agent 中间件：文件操作顺序、工具动态选择 | `file_ordering.py`、`tool_selection.py` |
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
| `services/` | `core/`、`database/`、`repository/`、`output/`、`memory/`、`backend/`、`middleware/`、`tool/` | `api/` |
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
| HITL 与副作用工具 | `core/hitl.py` 的审批清单 + `FilesystemPermission` | 写文件、删文件、Shell 等有副作用的操作必须走审批或权限边界 |
| 业务数据持久化 | `repository/` 的 `BusinessRepository` | `services/` 不直接写 SQL |

凭据相关有一条硬规则：`DEEPSEEK_API_KEY`、`DATABASE_URL`、`TAVILY_API_KEY` 等应用自身凭据**不能**注入给 Agent 执行的命令；给 Agent 的变量名集中在 `core/defaults.py` 声明，值只来自 `.env`。

## 5. 关键取舍与历史教训

### 5.1 `LocalShellBackend` 不是安全沙箱

默认后端允许本地文件和 Shell 能力，只适合本机开发。面向共享或不受信任用户部署前，必须替换为受控 SandboxBackend 或独立解析服务。这一条在 README 的“使用边界”里也对用户可见。

### 5.2 不要依赖上游框架按类型推断能力

DeepSeek / MiniMax 都通过 `ChatOpenAI` 适配。`deepagents` 会把 `ChatOpenAI` 这一类**按类名**判定为“能接受非 PDF 文件块”，从而绕过按模型能力（`ModelProfile`）做的门控；而 `ChatOpenAI` 的能力表只收录 `gpt-*` 系列，我们的模型名拿不到能力声明，所有门控默认放行。

结论：**涉及模型能力的判断，必须由本仓库显式声明，不能依赖框架自动推导。** 这条经验来自聊天附件功能的调研，详细证据见 `note/上传附件技术设计.md` 的 §3.2、§3.3。

### 5.3 内容块转换是框架职责，路由是应用职责

标准内容块到各家 provider 请求体的转换由 LangChain 提供，不需自己实现；但“该不该发这种块”和“被拒之后怎么降级”属于应用层职责。

### 5.4 消息长度的双重约束

- 业务侧：`MessageRequest.content` 限制 1–12000 字符。
- 框架侧：`read_file` 等工具结果有 token 阈值，超限会被截断或改成文件引用。两端都要考虑，不要只按其中一侧设计。

## 6. 数据库变更流程

1. 改 `database/schema.py` 的表定义；
2. 在 `database/migrations.py` 增加幂等迁移（`ADD COLUMN IF NOT EXISTS` 风格）并提升 `database/constants.py` 中的 schema 版本；
3. 执行 `uv run melonclaw-db-init` 应用迁移；
4. 服务启动时 `verify_schema` 只做校验，**不会**自动迁移。
