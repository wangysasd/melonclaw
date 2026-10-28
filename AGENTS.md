# MelonClaw 开发约定

MelonClaw 是一个持续演进的 Deep Agents 应用。开发工作应围绕可运行能力、清晰边界和可验证结果展开。
注意不要写兼容性代码，现在是开发阶段，可以清空数据库历史数据。
系统的字号以14号为主。
本文件是**地图**，不是说明书。先在下面定位，再去对应文档读细节；不要指望本文件包含全部细节。

## 知识地图

| 想了解 | 去哪读 |
|---|---|
| 模块分层、依赖方向、横切入口、关键取舍与历史教训 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Linux 服务器目录、迁移、升级与备份 | [docs/deployment.md](docs/deployment.md) |
| 前端结构、开发与部署、UI 约定 | [docs/FRONTEND.md](docs/FRONTEND.md) |
| 各领域质量评分、已知差距、环境陷阱 | [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md) |
| 设计文档清单与状态 | [docs/design-docs/index.md](docs/design-docs/index.md) |
| 执行计划的写法与存放约定 | [docs/exec-plans/README.md](docs/exec-plans/README.md) |
| 已知技术债 | [docs/exec-plans/tech-debt-tracker.md](docs/exec-plans/tech-debt-tracker.md) |
| 安装、配置、启动、功能使用、排障、安全边界 | [README.md](README.md) |

本地草稿目录 `note/` 不纳入版本控制，里面的内容只作参考，不作为事实依据。

## 工作方式

- 用户提供网页、文档或代码链接时，先阅读链接内容，再结合当前实现说明相关概念、设计取舍和关键代码。
- 用户要求新增能力时，应在现有应用上迭代、扩展或重构，保持 Web、数据库和 Agent 组装链路的一致性；不要为单个链接或单个功能创建独立项目。
- 新增功能完成后，更新 `note/note.md`，记录功能背景、问题定义、方案、关键取舍、实现位置、验证方式、可观察点和当前边界，语气让人容易理解。结论稳定后，把需要长期存在的部分搬进 `docs/`，并在 [docs/design-docs/index.md](docs/design-docs/index.md) 更新状态。
- README.md 面向首次使用者，持续维护安装、配置、启动、功能使用、排障和安全边界；不要把实现过程流水账堆进 README，实现细节放进 `docs/`。
- 模型只从数据库 `model_configs` 解析。初始化只写入无凭据、未启用的供应商模板，不创建默认模型、不读取模型环境变量。管理员配置的是全局内置模型，普通用户配置的是个人模型；没有可用模型时提示用户先配置，不从环境变量生成模型。供应商可显式配置 `api_key_env` 作为共享凭据兜底：全局模型按个人 Key → 数据库共享 Key → 指定环境变量解析，个人模型只使用个人 Key。
- 不得把 `.env` 中的 API Key、Token、数据库密码或完整凭据 URL 输出到终端、日志、文档、示例代码或回复中；示例只能读取环境变量。

## 代码与目录

- 应用代码统一维护在 `src/melonclaw/`，按配置、Agent 构建、提示词、工具、数据库、流式输出、Web 服务等职责拆分模块。
- 新增能力优先扩展既有模块或提炼可复用组件；只有职责足够独立时才在包内增加子包。目录和模块命名应描述领域职责。
- 不要把所有实现集中到单一文件；保持 Web、Agent、工具和持久化层的边界。
- **依赖方向由 `tests/test_architecture.py` 强制**，各包允许依赖谁见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 3 节。新增包时先在该测试的 `FORBIDDEN_IMPORTS` 里声明规则，再写实现。
- 横切关注点只从固定入口进入：配置走 `core/config.py`，模型目录走 `core/model_catalog.py`，模型实例走 `core/chat_model.py`，MCP 走 `core/mcp_config.py`，长期记忆走 `memory/MemoryService`，监控走 `core/tracing.py`（只用运行副本，不写业务表或缓存 Agent），业务数据读写走 `repository/`。不要另开旁路。
- 依赖以 `pyproject.toml` 和 `uv.lock` 为准，使用 `uv sync` 管理环境；`requirements.txt` 仅作为 pip 兼容清单维护。
- **数据库初始化与增量更新分开。** `uv run melonclaw-db-init` 只用于第一次系统上线，或重大变更明确决定放弃历史数据并重建数据库之后；该命令创建当前完整 schema、Checkpoint/Memory Store 并写入初始种子，不负责清库。新增功能若需要改表且应保留历史数据，必须新增按序、可审计的升级步骤，并通过 `uv run melonclaw-db-update` 执行；更新记录与每个升级步骤在同一事务提交，重复运行不重复执行已完成步骤，不删除用户业务数据。升级脚本集中在 `database/updates/`，包含必要的数据填充和约束检查；新数据库直接按 `database/schema.py` 创建，不在业务读取路径加入旧字段兜底。完成升级后校验 schema，并同步 README、架构文档、测试和本文件。本地开发允许在重大变更时清空历史数据，但须明确告知影响，且不得让 init 自动删除数据。

- 定时任务仅三张任务表（tasks、task_runs、task_operations），由 Web 生命周期内的 APScheduler 定期扫描并异步执行，不提供独立 Worker 命令。单实例数据库会话锁防止重叠执行器，执行状态写入复用持锁连接；不支持多进程 Web/滚动重叠部署，连接失效时停止任务，不自动重连接管。正常停服记 service_shutdown，启动收敛旧运行记 service_restarted，均不自动重跑。首次接入升级 `20261010_task_center` 一次创建当前完整三表；不维护未发布旧任务结构的迁移或回填，结构不一致时显式重建任务相关表，勿清理其他业务数据。独立任务通知 API、已读字段和索引已删除，启用状态只认 tasks.status。后续 20261015_task_reliability 保留数据升级移除通知结构、允许过期回执响应为空并增加清理索引；不得重建当前已登记的任务结构。计划固定北京时间，错过/重叠直接跳过；重新运行使用当前配置，不绑定项目或固定 Skill 集合。每个任务通过 tasks.conversation_id 绑定一个固定普通会话，执行消息与队列/结果同事务同步；后台 Agent 仍独立 task-run Checkpoint，正常聊天不收尾后台占位。创建/修改任务记录用户实际设置，每次触发只追加助手消息。后台与追问读取固定会话累计历史，按运行/用户消息绑定来源保证重试稳定；相邻同角色文本只在请求副本合并，本轮工具链保留。历史准备默认 60 秒/最多 4 次摘要，超限明确失败、取消传播；后台准备计入完整运行时限。历史过长用有界增量摘要和近期正文，read_task_history 仅允许读取本轮来源；成功完整结果才计进度。有限目标 complete_task 仅设置内存意图，成功终态且任务版本未变时暂停后续计划。任务会话受当前租户可见性校验，不能移入项目，删除前先归档并结束运行。数据库变更前处理活动运行并停止 Web。`/api/ready` 包含内置执行器就绪；API 与无人值守边界见 [docs/design-docs/task-center.md](docs/design-docs/task-center.md)。

- 定时任务每次真正执行时通过 `services/task_resources.py` 按所属用户当前权限解析有效 Skill/MCP，复用资源有效状态与来源/凭据/白名单规则，不绑定或保存资源内容快照。`backend/task_skills.py` 仅提供授权 Skill 正文/引用文件的只读路径，逐层拒绝符号链接，不开放主机/项目文件、Shell、脚本或文件产物。后台主/子 Agent 共用收尾指引、发现额度（默认 4 次）、模型/工具预算与停止标志，内部工具选择模型调用也计入共享预算。确认缺失后跳过并说明，无可继续工作时正常结束；缺资源不构成失败，succeeded 只表示工程正常结束，持续循环触发预算/时限为工程失败。有限目标仍须显式 complete_task，不凭运行成功自动暂停计划。详见 [当前资源与执行保护](docs/design-docs/task-resources-and-execution-guards.md)。

- 任务结果复用普通 AI 消息布局，不显示任务专属配置/引用按钮或任务名、触发/计划状态卡。任务运行元数据仅保留用于增量同步、生成/失败状态及普通聊天流隔离。追问统一使用累计历史，显式报告引用 API、字段与草稿已移除；不增加旧协议兼容或清理会话正文。配置/取消走定时任务，配置也可通过对话工具。全局导航不提供独立任务通知入口，前端不轮询通知接口，定时任务直接回调打开固定会话。任务名称直达会话，最近一次运行打开记录抽屉；不保留独立详情页，历史/快照/失败与取消在抽屉中查看，关闭保留筛选和页码。

- 对话任务配置通过普通聊天主 Agent 的 `configure_task`，先 inspect 读取真实配置/版本，再 save 部分字段；未提供字段保留，新建未指定模型使用管理员设置的全局系统默认模型；无可用系统默认时明确报错，不退回聊天模型、个人默认或第一条可用模型。编辑未指定模型则保留原模型。身份、当前会话和真实用户消息由运行时注入，禁止模型指定任意任务/用户/会话。新建绑定当前非项目会话，复用 TaskService 保存、版本和 task_operations 幂等事务，不重复插入用户摘要；表单仍记录实际设置摘要。明确配置意图才保存，缺少要素再提问；作为限定当前会话的受控写入不额外 HITL，不进入子 Agent/后台/PTC。聊天保存不立即执行，已有运行保持快照，后续使用新配置；完整操作回执默认保留 30 天，过期明确拒绝，周期只清空响应并永久保留幂等键/摘要，不能删键后允许旧请求再次执行；不增加表、兼容代码或清理历史。

## 配置与可移植性

- 不写入个人机器的绝对路径、用户名、数据库账号或本地密钥。项目根目录使用 `Path(__file__).resolve()` 或明确的配置项推导。
- 外部服务地址、模型、数据库、工作区和 Web 监听参数通过环境变量或根目录配置文件提供。
- `mcp.json` 保存内置 MCP 种子，`melonclaw-db-init` 把它单向同步进数据库，运行时只读数据库（`services/mcp.py` 装配 MCP，`skills` 表索引 Skill）。系统级 Skill 的唯一存储是 `MELONCLAW_DATA_DIR`（默认 `~/.melonclaw/data/`）下的 `skills/shared/`，新增/更新走资源管理 UI 或 skill_import 服务；运行时正文不得写入仓库；仓库 `.data/skills/shared/` 仅保存内置分发模板（`cicc-*`/`htsc-*` 除外），安装时复制，升级不得覆盖运行时数据。`users/`、`tmp/` 仅在外部数据根按需创建，仓库不保留这两个目录或占位文件。`.env` 只保存凭据和占位符值。MCP 配置必须支持无 MCP 时正常启动。
- 新增持久化路径时，区分临时 runtime、Web Project 持久 workspace、项目源文件和数据库数据，避免把用户数据写进仓库。持久数据根和工作区不得配置在代码目录内；Linux 部署约定见 `docs/deployment.md` 和 `ops/`，内置模板部署与迁移走 `melonclaw-resources`，不得覆盖已有用户资源。
- 保持跨用户运行所需的相对路径和用户目录默认值；不要假设仓库位于某个固定用户名目录下。

- 环境变量业务资源只读数据库，通过 `services/environment.py` 按管理员 global／普通用户 user 管理；个人启用覆盖共享，个人停用或删除回退共享。HOME/LANG/TZ/LC_* 与 PATH 自动提供，旧默认名单和 `MELONCLAW_SHELL_ENV_ALLOWLIST` 不再透传。聊天完整输入先脱敏，经 HITL 提交（管理员共享、普通用户个人），值不进入消息／Checkpoint／追踪；管理值仅回显前后各 4 位的脱敏预览，短值全部遮盖，数据库及备份按含凭据数据保护。新增 schema 更新为 `20261017_environment`。
- Agent 缓存使用 `repository/resource_versions.py` 的单进程全局／用户内存版本；新增资源写入口必须显式登记提交—发布边界，不能仅更新数据库而不失效。预热命中不查询资源版本、不扫描 Skill、不重新解析模型；准备身份每请求实时联查一次，会话／项目归属联查一次，同次准备复用，恢复请求重验。保留锁、幂等和事务内权限检查；手工 SQL／文件修改后重启，不支持多 Web 进程。

## Agent 能力与安全边界

- 普通聊天主/子 Agent 的 write_file、edit_file、delete 免逐次人工审批，不通过 ask_user 请求调用批准；文件工具仍受既有路径与权限规则约束，不进入 PTC。其他有副作用的工具必须明确经过 HITL 或受控权限边界；Shell 沿用仅限受信任本地开发的免审批例外。已启用且白名单允许的外部 MCP 工具免逐次审批，不通过 ask_user 请求调用批准；权限由配置作用域、启用状态、白名单和远端凭据限制，不进入 PTC。聊天安装与测试 MCP 配置仍走 HITL。
- PTC/Interpreter 只允许加入已经确认无需逐次审批且副作用明确受限的工具；不能因为主 Agent 配置了 HITL 就把写文件、Shell、数据库写入、发消息、交易或部署工具放入 PTC。
- 任何新增工具都要说明输入校验、权限范围、错误处理、敏感信息脱敏和是否进入审批清单；进入审批清单的工具在 `core/hitl.py` 中登记，不进清单的要写明理由。
- Web 使用 Cookie 会话，`user_id` 是与当前会话一致的页面身份断言；仅 profile=dev 开放全员显式切换（含 admin），仍是开发能力，不是生产权限隔离；服务层必须从用户记录解析唯一且有效的租户归属，并重新校验 Project、Conversation 和附件归属。Conversation 只归属 `user_id + project_id`，`tenant_id` 由服务端解析后用于 Agent 和 Memory 运行上下文。开发模拟用户不能被描述成生产认证系统。初次上线只有 `system` 租户下的 `admin`（种子写入）；其他用户由当前 system/admin 通过 `POST /api/admin/users` 创建并选择启用租户（member）；管理权限按当前身份校验，不按原登录身份保留。
- 当前 `LocalShellBackend` 不是安全沙箱。若新增面向共享环境的能力，必须说明隔离方案、授权边界和部署限制。
- 不依赖上游框架按类型自动推断模型能力。涉及多模态、文件类型、工具权限的判断必须由本仓库显式声明，原因见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 5.2 节。

## 文档与验证

- 前端 UI 改动前须阅读 `docs/FRONTEND.md` 和 `docs/design-docs/ui-consistency.md`。工作台页面顶栏统一复用 `frontend/src/components/WorkspaceHeader.tsx`（16px 标题、49px 顶栏），不得在单页 CSS 重新定义近似字号、高度或留白；基础参数继续来自 `tokens.ts` 与 `antd.ts`。拓展与定时任务顶栏右侧统一为“返回聊天”，安装、新增等业务操作放在正文工具栏右侧。

- 用户说明书 Skill `melonclaw-tutorial` 仅在用户显式要求根据新增或变化的项目功能更新时才修改；不要因功能变化主动更新该 Skill。此约定不影响 README.md 和 docs/ 的同步维护要求。`.data/skills/shared/melonclaw-tutorial/` 及其内容纳入 Git 版本管理，不得被 `.gitignore` 忽略。
- 新功能的设计分析先写入 `note/note.md`，可长期复用的结论搬进 `docs/design-docs/` 并更新索引；至少包含背景与目标、方案概览、关键设计选择、数据/事件流、失败与安全边界、运行步骤、预期结果和验证记录。
- 新增命令、环境变量、API、MCP 服务或用户可见行为时，同步更新 README.md；架构、依赖边界或横切入口变化时，同步更新 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 修改依赖、数据库结构或运行入口后，给出简洁的初始化/运行命令和验证结果。首次建库执行 `uv run melonclaw-db-init`；保留历史数据的 schema 升级执行 `uv run melonclaw-db-update`；服务启动不会自动建表或升级。
- 提交前运行 `scripts/check.sh`（后端编译、测试、lint，前端 lint、类型检查、测试，文档链接校验）。前端依赖未安装时脚本会跳过前端部分。
- 需要真实外部 API、数据库或 MCP 服务的验证，应明确依赖和观察点；不要在日志或回复中暴露凭据。

## 检查命令

```bash
scripts/check.sh              # 全部（推荐）
scripts/check.sh backend      # 只跑后端
scripts/check.sh frontend     # 只跑前端
```

等价的手工命令：

```bash
uv run python -m compileall -q src
uv run pytest -q tests        # pytest 与 ruff 声明在 pyproject.toml 的 dev 依赖分组中
uv run ruff check src tests
uv lock --check
git diff --check
cd frontend && npm run lint && npm run typecheck && npm test
```

新增或调整检查项时，同时改三处：`scripts/check.sh`、`.github/workflows/check.yml` 和本节。更多环境陷阱见 [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md)。
