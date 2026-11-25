# MelonClaw 开发约定

MelonClaw 是持续演进的 Deep Agents 应用。开发围绕可运行能力、清晰边界和可验证结果展开。本文件维护全局约束与阅读入口；领域契约在对应文档中维护。

## 项目原则

- 在现有应用上迭代、扩展或重构，保持 Web、数据库和 Agent 组装链路一致；不为单个链接或功能创建独立项目。
- 不添加旧协议、旧字段或未发布结构的兼容分支。数据库迁移与兼容代码分别处理，数据变更遵守下方规则。
- 用户提供网页、文档或代码链接时，先读链接内容，再结合当前实现说明概念、取舍与关键代码。
- 不将 API Key、Token、数据库密码或完整凭据 URL 输出到终端、日志、文档、示例代码或回复；示例只读取环境变量。

## 知识地图

按改动类型先读对应文档；只涉及局部功能时，无需通读所有领域文档。

| 改动类型 / 想了解 | 必读入口 |
|---|---|
| 模块分层、依赖方向、横切入口、新增包 | [架构](docs/ARCHITECTURE.md) |
| 前端页面、组件、样式与交互 | [前端](docs/FRONTEND.md)、[UI 一致性](docs/design-docs/ui-consistency.md) |
| 配置来源、模型与凭据 | [配置](docs/configuration.md)、[模型目录](docs/design-docs/custom-models.md) |
| Skill / MCP、持久文件 | [资源管理](docs/design-docs/user-skills-and-mcp.md)、[持久文件](docs/design-docs/persistent-user-files.md) |
| Agent 环境变量、准备流程或缓存失效 | [环境变量与缓存](docs/design-docs/environment-and-preparation.md) |
| 工具、审批与 PTC | [用户交互与审批](docs/design-docs/user-input-hitl.md)、[按需工具池](docs/design-docs/on-demand-tool-pool.md) |
| 用户、租户与会话身份 | [账户与登录](docs/design-docs/user-management-login.md)、[唯一租户](docs/design-docs/one-tenant-per-user.md) |
| 定时任务、对话配置任务、后台 Agent | [定时任务](docs/design-docs/task-center.md)、[当前资源与执行保护](docs/design-docs/task-resources-and-execution-guards.md) |
| 知识库、固定嵌入、导入、检索与引用 | [知识库设计](docs/design-docs/knowledge-base.md) |
| 普通聊天附件 | [附件处理](docs/design-docs/chat-attachment-processing.md)、[存储与绑定](docs/design-docs/chat-attachments.md) |
| 数据库变更、部署、升级与备份 | [架构](docs/ARCHITECTURE.md)、[部署](docs/deployment.md)；知识库另读 [pgvector 安装](ops/PGVECTOR.md) |
| 复杂改动、分阶段实施 | [执行计划规范](docs/exec-plans/README.md) |
| 其他设计、质量差距与环境陷阱 | [设计索引](docs/design-docs/index.md)、[质量评分](docs/QUALITY_SCORE.md)、[技术债](docs/exec-plans/tech-debt-tracker.md) |
| 安装、启动、功能使用与排障 | [README](README.md) |

## 全局约束

### 代码边界与固定入口

- 后端位于 `src/melonclaw/`，前端位于 `frontend/`。优先扩展既有模块或提炼可复用组件，保持 Web、Agent、工具、持久化分层；职责独立时才新增包，避免单文件集中实现。
- 依赖方向由 `tests/test_architecture.py` 强制。新增包先在 `FORBIDDEN_IMPORTS` 声明规则，允许依赖关系见架构文档。
- 下列后端路径均相对 `src/melonclaw/`：配置走 `core/config.py`；模型目录走 `core/model_catalog.py`，实例走 `core/chat_model.py`；MCP 数据库装配走 `services/mcp.py`，配置校验与白名单走 `core/mcp_config.py`；长期记忆走 `memory/MemoryService`；监控走 `core/tracing.py`；业务数据读写走 `repository/`。不另开旁路。
- 监控只使用运行副本，不写业务表或缓存 Agent。新增资源写入口必须遵守 `repository/resource_versions.py` 的提交—发布失效边界，不能仅改数据库而不失效缓存。
- 缓存预热命中不查询资源版本、不扫描 Skill、不重新解析模型；每请求实时解析身份与会话/项目归属，同次准备复用，恢复请求重验。保留锁、幂等和事务内权限检查；手工 SQL/文件变更后重启。
- 依赖以 `pyproject.toml`、`uv.lock` 为准，用 `uv sync` 管理；`requirements.txt` 仅维护为 pip 安装清单。

### 配置来源

| 对象 | 唯一来源与边界 |
|---|---|
| 部署参数 | 数据库连接、数据根、工作区、Web 监听等通过环境变量或明确配置项提供，由 `core/config.py` 统一解析；不在业务模块直接读环境变量 |
| 聊天模型 | 只从数据库 `model_configs` 解析。管理员管理全局模型，普通用户管理个人模型；无可用模型时提示先配置，不从环境变量生成模型 |
| 聊天模型凭据 | 全局模型按个人 Key → 数据库共享 Key → 供应商显式 `api_key_env` 解析；个人模型只用个人 Key。环境变量兜底仅补凭据 |
| 知识库嵌入 | 开发侧根 `.env` 的 `KNOWLEDGE_EMBEDDING_API_KEY`、`KNOWLEDGE_EMBEDDING_URL`、`KNOWLEDGE_EMBEDDING_MODEL_NAME`、`KNOWLEDGE_EMBEDDING_DIMENSIONS`，统一经 config 进入 `core/knowledge_embedding.py`；工厂不硬编码具体值或默认值，不读个人/数据库 Key，不使用 SDK 隐式兜底 |
| 文档 OCR | 开发固定 DeepSeek 官方 OCR，由 `core/knowledge_ocr.py` 消费 Settings 中的 `DEEPSEEK_API_KEY`；不从聊天模型目录选择，不开放模型配置页面 |
| MCP | Web 页面写数据库，`services/mcp.py` 装配；无 MCP 时正常启动。初始化只创建 MCP 表结构 |
| Agent 环境变量资源 | 只从业务数据库经 `services/environment.py` 读取，管理员 global、普通用户 user；个人启用覆盖共享，停用/删除回退共享。基础 HOME/LANG/TZ/LC_* 与 PATH 自动提供，不恢复旧环境透传名单 |

- 初始化只写入无凭据、未启用的供应商模板，不创建默认模型，不从环境变量导入模型。
- 知识库嵌入与解析均不向用户或管理员开放配置/选择入口。内部模型引用仅用于嵌入外键，不保留解析模型字段。嵌入维度来自配置，但必须匹配当前 `vector(1024)`；更改维度需要独立结构升级及向量重建。
- Agent 环境变量聊天输入先脱敏，再经 HITL 提交；值不进入消息、Checkpoint 或追踪。管理页只显示前后各 4 位的脱敏预览，短值全部遮盖；数据库与备份按含凭据数据保护。

### 数据库与持久数据

- **初始化、升级与重建分开。** 首次空库，或明确重建完成后的空库，执行 `uv run melonclaw-db-init` 创建完整 schema、Checkpoint/Memory Store 和种子；该命令永不自动清库。Web 启动不自动建表或升级。
- 已有数据默认保留：结构变更在 `database/updates/` 新增按序、可审计的升级，通过 `uv run melonclaw-db-update` 执行。每步变更与更新记录同事务提交，重复执行跳过已完成步骤；不改写已登记升级，不在读取路径兜底旧字段。
- 本地开发重大变更允许明确决定放弃指定范围的历史数据后重建，执行前告知影响并按部署约定备份；不得将“开发阶段”视为任意清库授权。数据库变更前处理活动运行并停止 Web，完成后校验 schema、同步相关文档和测试。
- 不把个人绝对路径、用户名、数据库账号或本地密钥写入项目文件。根路径从 `Path(__file__).resolve()` 或配置推导；数据根、持久工作区必须在代码目录外，区分临时 runtime、Project workspace、项目源码和数据库数据。
- Skill 由 `skills` 表索引，系统级运行时正文唯一存储为 `MELONCLAW_DATA_DIR`（默认 `~/.melonclaw/data/`）下的 `skills/shared/`。新增/更新经资源 UI 或 skill_import；内置模板分发与迁移经 `melonclaw-resources`，不得覆盖已有用户资源。
- 仓库 `.data/skills/shared/` 仅保存内置分发模板（`cicc-*`/`htsc-*` 除外），不写运行时正文。`users/`、`tmp/` 仅在外部数据根按需创建，仓库不保留目录或占位文件。

### 身份、工具与运行安全

- Web 使用无状态 JWT。浏览器把 `access_token` 放在 `localStorage`，受保护请求携带 `Authorization: Bearer`。令牌只含 `sub` 和 `exp`；`user_id` 仍只是与令牌一致的页面身份断言。服务端先验签和过期，再确认用户仍为 `active`，并从用户记录解析唯一租户，重验 Project、Conversation、附件归属。Conversation 归属 `user_id + project_id`，运行时 `tenant_id` 由服务端注入。登出、改密和停用租户不提前作废已签发令牌；删除用户会在下一次请求失效。
- 仅 dev profile 开放全员显式切换（含 admin），不得描述为生产权限隔离；权限按当前身份检查，不保留原登录身份的管理员权限。初次上线仅种子 `system/admin`，其他用户由当前系统管理员创建。
- 普通聊天主/子 Agent 的 `write_file`、`edit_file`、`delete` 及已启用、白名单允许的 MCP 工具免逐次审批，不用 `ask_user` 请求调用批准；仍受路径、作用域、启用状态、白名单和远端凭据约束。聊天安装/测试 MCP 配置仍走 HITL。
- 其他有副作用工具须有明确 HITL 或受控权限边界。Shell 的免审批例外仅适用于受信任本地开发；`LocalShellBackend` 不是安全沙箱，共享部署能力须明确隔离方案与限制。
- PTC/Interpreter 仅纳入已确认无需逐次审批且副作用明确受限的工具；文件写入、Shell、数据库写入、发消息、交易、部署及外部 MCP 不进入 PTC。不能依赖主 Agent 的 HITL 为 PTC 内调用提供保护。
- 新增工具须说明输入校验、权限、错误处理、脱敏、审批及 PTC 资格；需审批的在 `core/hitl.py` 登记，免审批须注明理由。多模态、文件类型和工具权限由本仓库显式声明，不依赖上游类型推断。
- 当前仅支持单 Web 进程，不支持多进程或滚动重叠部署。定时任务在 Web 生命周期内执行，无独立 Worker；锁连接失效即停止，不重连接管，停服/重启终止的运行不自动重跑。
- 后台任务按所属用户当前权限解析 Skill/MCP，不存资源内容快照；Skill 只读且逐层拒绝符号链接，不开放主机/项目文件、Shell、脚本或文件产物。主/子共用预算与停止保护，缺资源如实说明；运行成功不等于有限目标完成，暂停计划须显式 `complete_task` 并通过终态/版本检查。
- 对话 `configure_task` 仅限普通聊天主 Agent 操作当前非项目会话，运行时注入身份与真实用户消息；先 inspect 后 save，复用 TaskService 的版本/幂等事务。属于受控写入，无额外 HITL，不进入子 Agent/后台/PTC；新建省略模型只用全局系统默认，缺失时明确报错。

### 前端一致性

- 正文基准 **14px**，标题等层级使用 `frontend/src/theme/tokens.ts` 与 `antd.ts`，不将所有文字强制设为同一字号。
- 工作台顶栏复用 `WorkspaceHeader.tsx`（16px 标题、49px 顶栏），不在单页 CSS 重定义近似尺寸。拓展与定时任务顶栏右侧为“返回聊天”，业务操作放正文工具栏右侧。

## 开发与文档流程

1. 按知识地图阅读相关契约。跨 3 个以上模块、改数据库结构、多轮或分阶段工作按执行计划规范建计划；单文件小改不强制建计划。
2. 新功能开发前在 `note/note.md` 记录背景、问题、目标与方案；完成后补关键取舍、实现位置、数据/事件流、失败与安全边界、运行步骤、预期结果、验证和可观察点。`note/` 不纳入版本控制，仅作草稿参考。
3. 稳定结论同步 `docs/design-docs/` 并更新索引；架构/依赖/横切入口变化同步架构文档。执行步骤与有日期的验证记录写 `docs/exec-plans/`，遗留缺口写技术债；正式文档不得以 note 为事实来源。
4. 新增命令、环境变量、API、MCP 服务或用户可见行为时同步 README。README 面向首次使用者，只维护安装、配置、启动、使用、排障与安全边界，实施细节放 docs。
5. 完成检查后交付实际改动、验证结果与限制；依赖、schema 或入口变化须给出相应初始化/升级/运行命令。

用户说明书 Skill `melonclaw-tutorial` 仅在用户显式要求更新时修改；其目录 `.data/skills/shared/melonclaw-tutorial/` 纳入 Git，不得被忽略。此限制不影响 README 与 docs 的同步维护。

**本文件维护规则：** 只有全局开发约束、必读入口或检查方式改变时更新 AGENTS.md。领域字段、格式白名单、默认预算、迁移编号和验收流水放对应文档；更新规则时直接修改原条目，不在末尾追加“覆盖前文”。历史证据归档后不得继续标为当前能力。

## 检查命令

提交前运行：

```bash
scripts/check.sh              # 全部（推荐）
scripts/check.sh backend      # 开发中定向检查后端
scripts/check.sh frontend     # 开发中定向检查前端
```

检查项以 `scripts/check.sh` 为唯一命令清单：后端编译/测试/lint、锁文件、前端 lint/类型检查/测试、空白错误；文档链接和架构依赖由后端测试覆盖。使用 `uv run` 执行 Python 工具。新增/调整检查入口时同步脚本与本节，环境陷阱见质量文档。

前端依赖未安装时脚本会跳过前端；数据库与外部服务测试也可能因缺环境跳过。交付时明确区分通过、失败、跳过和未运行，不能将跳过算作通过。真实 API、数据库或 MCP 验证须说明依赖与观察点，记录不得包含凭据。
