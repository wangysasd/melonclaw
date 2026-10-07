# MelonClaw

<p align="center">
  <img src="frontend/public/assets/brand/melonclaw-readme.png" alt="MelonClaw" width="314" height="314" />
</p>

**基于 Deep Agents 的通用 AI 助手，在浏览器中完成研究、写作、文件处理和多步骤任务。**

MelonClaw 将模型、工具、Skill、MCP、项目文件和长期记忆放在同一个工作界面中。你可以查看执行过程、回答助手的问题，并在文件修改、Shell 执行和外部工具调用前决定是否允许。

当前适合本机开发与个人使用：页面提供账号密码登录及全员开发身份切换，默认的 `LocalShellBackend` 不是安全沙箱。

## 项目特点

| 能力 | 可以做什么 |
|---|---|
| 多模型管理 | 管理员发布内置模型，用户配置个人模型与 Key；聊天时切换模型，分别设置默认模型和上下文窗口 |
| 多步骤任务 | Agent 制定任务清单、委派子 Agent，按需查找工具；展示执行阶段、工具结果、耗时和模型实际返回的思考内容 |
| 人工确认与主动提问 | 敏感操作执行前审批，可允许、改参或拒绝；信息不足时通过问题卡补充需求后继续 |
| Skill 复用 | 从 ZIP 或 GitHub 安装技能、预览差异并更新；也可通过聊天将工作流程整理成新 Skill，审批后保存 |
| MCP 扩展 | 接入 HTTP、SSE 和管理员配置的 stdio 服务，测试连接、限制工具白名单；支持聊天安装个人 MCP |
| 项目与持久工作区 | 普通会话独享目录，项目内会话共享文件；PostgreSQL 保存会话、运行状态和多级长期记忆 |
| 附件与成果 | 上传图片、文本、带文本层的 PDF 和 Office 文档；预览、下载生成的文件、图片、Markdown 与自包含 HTML |
| 结构化展示 | 表格复制与 CSV 下载、Mermaid 图形与 SVG 导出、修改差异、来源引用、折线图和柱状图 |
| 运行控制 | 按模型窗口压缩上下文，限制模型步骤与工具调用，重试瞬时模型错误，汇总已上报的输入／输出 token |

技术栈：Python / Deep Agents / LangChain / FastAPI / PostgreSQL，前端使用 React / TypeScript / Vite。

## 快速开始

### 1. 准备环境

- Python 3.11+（低于 4.0）和 `uv`。
- PostgreSQL 14+，已启动且允许当前应用账号连接、建表。
- Node.js 20+ 和 npm。
- 本地启动脚本需要 Bash、`curl`、`lsof`，适用于 macOS / Linux；Windows 可在 WSL 中运行。
- 一个支持 OpenAI 兼容聊天接口及工具调用的模型服务。**初始化不创建默认模型**，启动后在页面配置。

以下命令均从项目根目录执行。首次使用先创建本地配置；已有 `.env` 时保留原文件：

```bash
cp .env.example .env
uv sync --locked
npm --prefix frontend ci
```

### 2. 配置并初始化数据库

先创建数据库，例如在 PostgreSQL 本地认证已经配置好的情况下执行：

```bash
createdb melonclaw
```

在 `.env` 的 `DATABASE_URL` 中填写你自己的连接信息。格式为 `postgresql+asyncpg://<用户>:<密码>@<主机>:<端口>/<数据库名>`；尖括号表示待替换字段，不要原样保留。用户名或密码中的 URL 特殊字符需要百分号编码。不要将实际连接串贴到日志、聊天或版本库中。

首次运行只需填好 `DATABASE_URL`；模型 Key 暂时不用写入 `.env`，Tavily 和 MCP 也都可暂不配置。

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

此命令创建业务表、Checkpoint 和 Memory Store，写入 `system` 租户下的管理员 `admin`（初始密码固定为 `admin`，数据库保存哈希）、23 家未启用且无凭据的供应商模板，并从共享 Skill 目录及可选 `mcp.json` 补录资源。首次上线使用该命令。**服务启动只校验表结构，不会建表或升级。**

### 3. 启动前后端

```bash
scripts/start.sh
```

- 浏览器打开 [MelonClaw](http://127.0.0.1:8001)。
- 后端默认地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。
- 用用户 ID `admin` 和初始密码 `admin` 登录，在左下角“系统状态”检查就绪情况。`/api/status` 现在也要求登录 Cookie，匿名请求返回 401。

`ready` 表示服务就绪，不表示模型已配置或外部 MCP 已连通。

### 4. 配置第一个模型

1. 在全局导航底部的「更换用户」菜单选择管理员，打开「拓展 → 模型」。也可从聊天模型选择器的「添加自定义模型」进入。
2. 选择供应商卡片，核对 **Base URL**，填写 **API Key**，将状态设为启用并保存。
3. 进入该供应商的「管理模型」，从远程列表添加，或手动填写供应商实际接受的模型名称。
4. 按服务商实际能力填写**上下文窗口**，启用模型，并按需设为默认。新模型默认填写 1,000,000 tokens（1M），这只是应用默认值，不能代表服务商实际支持 1M。
5. 返回聊天，选择模型并发送一条消息。能收到回复，才算完成模型连通验证。

聊天中的用户头像与导航栏一致，使用当前用户的姓名缩写和专属配色。

管理员配置的模型显示在「内置模型」，可供所有用户使用；普通用户在已有供应商上填写个人 Key 后可添加「自定义模型」，仅本人可用。没有可用模型时仍可启动和管理配置，但不能聊天。

## 启动、停止与更新

```bash
scripts/start.sh                         # 默认 dev：读取 .env 启动前后端
scripts/start.sh --profile prod          # 读取 .env.prod 启动前后端
scripts/restart.sh --profile prod        # 读取 .env.prod 重启前后端
scripts/restart.sh                       # 默认 dev：读取 .env 重启前后端
scripts/shutdown.sh                      # 停止前后端
scripts/start.sh frontend                # 仅启动前端
scripts/restart.sh frontend --profile prod # 仅重启前端
scripts/shutdown.sh frontend   # 仅停止前端
```

启动和重启脚本的 `--profile` 默认值为 `dev`：dev 读取根目录 `.env`，prod 读取 `.env.prod`；显式指定 `MELONCLAW_ENV_FILE` 时优先读取该文件。脚本将所选 profile 传给后端。直接运行 `uv run melonclaw-web` 时仍读取 `.env` 中的 `profile`。

开发时也可在两个终端分别以前台方式运行，直接查看输出：

```bash
# 终端一：后端
uv run melonclaw-web
```

```bash
# 终端二：前端
npm --prefix frontend run dev
```

一键脚本的日志默认位于 `${TMPDIR:-/tmp}/melonclaw-dev/backend.log` 和同目录的 `frontend.log`。可在启动终端设置 `MELONCLAW_LOG_DIR` 指定日志目录，`MELONCLAW_RUN_DIR` 指定 PID／运行目录；启动、停止、重启必须使用一致的环境变量。端口被占用时脚本拒绝启动；先确认占用进程，若为本项目服务再运行停止命令。

更新代码后运行 `uv sync --locked` 和 `npm --prefix frontend ci`，再重启。新增功能需要调整数据库、且要保留历史数据时，先运行 `uv run melonclaw-db-update`；它只应用尚未执行的版本化更新，不删除业务数据。全新数据库仍运行 `uv run melonclaw-db-init`。

`melonclaw-db-init` 用于第一次系统上线，或重大变更决定放弃历史数据并重建数据库时。它不会主动清库；清库需在确认目标开发库、备份所需数据后自行执行。清空会删除用户、模型配置、会话、Checkpoint 和 Memory 等数据库内容；工作区、附件和 Skill 正文在数据库外，不会随之删除。重建后需重新配置模型及非种子用户。

### Python 命令入口（`src/melonclaw/main_*`）

这些文件是应用的命令入口，命令名称在 `pyproject.toml` 中注册。安装依赖后，从项目根目录用 `uv run` 执行：

| 入口文件 | 命令 | 作用与使用时机 |
|---|---|---|
| [main_web.py](src/melonclaw/main_web.py) | `uv run melonclaw-web` | 启动 Web 后端，提供页面所需的 API 和 Agent 服务；前端需另外启动或部署。启动只校验数据库结构，不自动初始化或升级。 |
| [main_db_init.py](src/melonclaw/main_db_init.py) | `uv run melonclaw-db-init` | 首次建库：创建当前完整业务表、Checkpoint 和 Memory Store，写入初始 admin、供应商模板和资源索引。不会主动清库；已有 admin 会跳过，不会把修改后的密码重置为 `admin`。 |
| [main_db_update.py](src/melonclaw/main_db_update.py) | `uv run melonclaw-db-update` | 已有数据库升级：按序执行尚未应用的数据库更新并校验结构，保留历史业务数据；重复执行不会重跑已完成步骤。 |
| [main_password.py](src/melonclaw/main_password.py) | `uv run melonclaw-admin-password` | 忘记 admin 密码、无法登录时的终端恢复入口。交互输入并确认新密码，写入密码哈希并撤销相关会话。正常登录后可直接在头像菜单修改密码，无需执行此命令。 |
| [main_resources.py](src/melonclaw/main_resources.py) | `uv run melonclaw-resources --service-stopped <子命令>` | 停止服务后安装缺失的内置 Skill 模板，或复制迁移持久目录。不会覆盖已有用户资源，也不会删除源目录；`--service-stopped` 是操作者的停服确认，不会自动停止服务。 |

资源命令有两个子命令：

```bash
# 把缺失的内置 Skill 安装到配置的数据根目录；首次安装后再运行 db-init 登记索引
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared

# 复制持久目录；将占位路径替换为实际目录，完成后按部署文档切换配置
uv run melonclaw-resources --service-stopped migrate --source <原目录> --destination <目标目录>
```

目录迁移的停服、配置切换与校验步骤见[部署文档](docs/deployment.md)。

## 配置文件与填写说明

### 配置放在哪里

| 位置 | 保存内容 | 修改后如何生效 |
|---|---|---|
| 根目录 `.env` | 数据库连接、可选搜索凭据、目录与运行参数 | 重启后端；助手名称需重启前端 |
| 页面「模型」 | 供应商地址、Key、模型名称、默认模型、上下文窗口 | 保存后供后续新任务使用 |
| 页面「连接器」 | 日常使用的 MCP 连接、启用状态、工具白名单 | 保存并启用后，下一条新消息使用 |
| 根目录 `mcp.json` | 内置共享 MCP 的初始化种子 | 执行 `uv run melonclaw-db-init` 补录缺失项；不覆盖已有项 |
| `MELONCLAW_DATA_DIR/skills/` | 共享与个人 Skill 正文 | 日常通过管理页或聊天审批安装、更新，保持文件与索引一致 |

后端读取根目录 `.env`，已导出的同名环境变量优先。`.env` 不提交 Git；示例模板见 [.env.example](.env.example)。模型目录只从数据库 `model_configs` 解析，设置模型名称环境变量不会自动创建模型。

### 基础配置

| 变量 | 默认值 | 填写说明 |
|---|---|---|
| `DATABASE_URL` | 无，必填 | PostgreSQL 连接串，使用 `postgresql+asyncpg` 驱动；数据库须事先创建 |
| `TAVILY_API_KEY` | 空 | Tavily 联网搜索凭据；留空仍能启动，但相关搜索能力不可用 |
| `MELONCLAW_NAME` | `MelonClaw` | 聊天中的助手名称，留空也使用默认值；前端启动时读取 |
| `MELONCLAW_WORKSPACE_DIR` | `~/.melonclaw/workspaces` | 会话／项目持久工作区，包含附件和输出文件 |
| `MELONCLAW_DATA_DIR` | `~/.melonclaw/data` | Skill 等平台资源的数据根，与 Agent 工作区分离 |
| `MELONCLAW_USER_INPUT_TTL_SECONDS` | `86400` | 用户问题卡的有效期，单位秒 |
| `profile` | 空 | 严格等于 `dev` 时显示并启用“免密登录”和侧栏“切换用户”；空、未配置或其他值关闭 |
| `brand` | 空 | 仅值为 `rms`（忽略大小写）时显示全局导航中的 AMP（A）和 Mindera（M），启用左侧浅蓝玻璃环配图、右侧带柔和蓝色光晕的居中 Logo 和精简表单的 RMS 登录页（窄屏只显示 Logo 和表单），并切换欢迎页/侧栏字标、助手头像及 Browser Tab 标题/favicon（`RMS · 投研助手`）；其他值（含空值）隐藏入口并使用 MelonClaw 浅蓝登录页：左侧品牌、标题与表单，右侧玻璃 AI 环配图，窄屏只显示表单。dev 使用 `.env`，prod 使用 `.env.prod`，修改后重启前端或重新构建 |

首次安装时，在初始化前把所需内置技能部署到数据根下的 `skills/shared/`，包括 `.data/skills/shared/melonclaw-tutorial/` 和 `.data/skills/shared/skill-creator/`；不要将数据根直接指向某个 Skill 目录。仓库 `.data/skills/shared/` 仅作为随版本分发的内置模板，不是运行时存储。升级不得覆盖数据根中已有 Skill。个人技能保存在 `skills/users/`，临时导入文件在 `skills/tmp/`。备份时需同时考虑数据库、数据根和工作区。

内置 Skill 安装是独立的 Python 运维命令，**服务启动时不会自动执行复制**。命令入口是 [main_resources.py](src/melonclaw/main_resources.py)，复制逻辑是 [storage/deployment.py](src/melonclaw/storage/deployment.py) 中的 `install_builtin_skills()`。

`install-builtins` 将仓库 `.data/skills/shared/` 中的模板复制到 `MELONCLAW_DATA_DIR/skills/shared/`（默认 `~/.melonclaw/data/skills/shared/`）。目标已存在同名 Skill 目录时，整个 Skill 跳过，保留已有内容，避免覆盖管理员的修改。此命令只安装文件，不写数据库。

首次上线时，在服务尚未启动、没有写入任务的情况下执行安装命令，再初始化全新数据库。`--service-stopped` 表示操作者已确认停止写入，命令不会替你停止服务。`melonclaw-db-init` 只扫描外部运行目录 `MELONCLAW_DATA_DIR/skills/` 登记 Skill，不同时扫描仓库模板目录：

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

已有部署迁移目录时先停止所有应用进程和写入；使用下列命令复制并校验，遇到不同内容拒绝覆盖，源目录保留：

```bash
uv run melonclaw-resources --service-stopped migrate --source .data --destination ~/.melonclaw/data
```

已有部署切换路径时，先停止服务，备份原数据目录，将其完整复制到新的外部数据目录（包含隐藏文件和导入草稿，遇到同名不同内容先解决冲突），再修改 `MELONCLAW_DATA_DIR` 并重启；不要重新初始化或清空数据库。数据库中的 Skill 路径为相对路径，无需改表。若原配置显式指向仓库 `.data/`，必须同步修改该配置。工作区同理：复制原工作区后设置 `MELONCLAW_WORKSPACE_DIR`。升级只替换代码、安装依赖和执行必要的 `melonclaw-db-update`，保留数据目录、工作区和 PostgreSQL 数据；数据库同时保存 Memory、聊天和文件索引。显式配置的目录也应位于代码目录之外，容器部署需挂载持久卷。

身份以服务端 Cookie 会话为准，原 `MELONCLAW_IDENTITY_HEADER` 入口已移除。使用同源 `/api` 反代并保留 Host；写请求会校验来源。

### 监听地址与前端代理

**一键脚本和 Vite 的端口／代理参数读取进程环境，不会自动从根目录 `.env` 加载。** 为避免前后端端口不一致，修改以下参数时在启动终端 `export`，停止和重启时也保留相同配置：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MELONCLAW_HOST` | `127.0.0.1` | 后端监听地址 |
| `MELONCLAW_PORT` | `8000` | 后端端口；Vite 默认据此确定代理目标 |
| `MELONCLAW_FRONTEND_HOST` | `127.0.0.1` | 前端开发服务器监听地址 |
| `MELONCLAW_FRONTEND_PORT` | `8001` | 前端开发服务器端口 |
| `MELONCLAW_API_TARGET` | `http://127.0.0.1:8000` | Vite 的后端代理地址；未设置时跟随 `MELONCLAW_PORT` |
| `MELONCLAW_ALLOWED_ORIGINS` | 本地开发来源 | CORS 与写请求共用的精确 Origin 白名单，逗号分隔，不支持 `*`；只支持同站跨域，跨站部署使用同源代理 |

例如，先停止旧端口上的服务，再设置新端口：

```bash
scripts/shutdown.sh
export MELONCLAW_PORT=8100
export MELONCLAW_FRONTEND_PORT=8101
scripts/start.sh
```

此时访问 [http://127.0.0.1:8101](http://127.0.0.1:8101)。默认前端通过同源 `/api` 代理访问后端，无需配置跨域。独立构建前端的 `VITE_API_BASE_URL`、代理与部署说明见 [前端文档](docs/FRONTEND.md)。不要将任何凭据放入 `VITE_*` 变量，它们会进入浏览器代码。

### 模型与供应商配置

供应商连接统一由管理员维护，当前支持 OpenAI 兼容接口。在供应商编辑页填写：

| 字段 | 填写方法与含义 |
|---|---|
| Provider ID / 展示名称 | ID 是稳定标识，创建后不可编辑；展示名称用于页面区分供应商 |
| Base URL | 服务商提供的 API 基础地址，例如包含 `/v1` 的基础路径；不要填写完整聊天接口路径 |
| API Key | 在页面输入实际 Key，只保存不回显；编辑时留空保留已有 Key |
| API Key Env | 可选，填服务器环境变量**名称**，不是 Key 内容；名称须大写且以 `_API_KEY` 或 `_ACCESS_TOKEN` 结尾 |
| Models Endpoint | 可选模型列表完整地址；留空使用 Base URL 下的 `/models` |
| 状态 | 管理员共享供应商是否启用；停用并保存会清除数据库共享 Key 和 API Key Env 引用，个人 Key 不受影响 |
| 请求头 JSON | 可选额外请求头；只写不回显，留空保留，`{}` 清空 |
| 思考输出格式 | 默认标准 OpenAI；可按接口实际返回格式选择 DeepSeek 独立字段、MiniMax think 标签或 OpenRouter 详情字段 |
| 扩展配置 JSON | 供应商要求的额外请求体；不能覆盖模型、消息、工具、流式协议或写入凭据 |

「测试连接」使用当前表单请求模型列表，不保存表单，也不验证聊天、工具调用或图片能力。接口不提供模型列表时，可按服务商提供的准确名称手动添加，再通过实际聊天验证。

凭据与默认模型规则：

- **内置模型**：个人 Key → 数据库共享 Key → 供应商明确指定的环境变量。只有配置了 `API Key Env` 才会读取该变量，不会扫描环境自动创建模型。
- **个人模型**：仅使用个人 Key，不借用共享凭据；供应商全局开关不影响个人模型，清除个人 Key 后个人模型不可用。
- **默认选择**：优先可用的个人默认模型，其次内置默认模型；都不可用时先选可用内置模型，再选个人模型。聊天中可临时切换。
- **上下文窗口**：每个模型单独存储；按实际服务能力调整，数值过大可能导致服务商拒绝超长请求。

思考格式选择会写入扩展配置的 `_melonclaw.reasoning_format`，仅供本地解析，不发送给供应商；开启模型思考所需的其他字段按服务商接口要求填写。只有模型实际返回可读思考时，页面才显示思考过程。详见 [模型设计](docs/design-docs/custom-models.md)与[推理协议](docs/design-docs/response-fluency.md)。

### Agent 运行与工具选择

以下变量可放入 `.env`，修改后重启后端。除单独注明外，数值为正整数；布尔值接受 `true/false` 或 `1/0`。

| 变量 | 默认值 | 含义 |
|---|---:|---|
| `MELONCLAW_AGENT_OUTPUT_RESERVE` | `4096` | 为模型输出预留的 token |
| `MELONCLAW_AGENT_SUMMARY_TRIGGER_RATIO` | `0.8` | 摘要触发比例，必须大于 0 且小于 1；阈值为模型上下文窗口 × 此比例 |
| `MELONCLAW_AGENT_SUMMARY_KEEP_TOKENS` | `4096` | 摘要时保留的近期上下文 token 预算，须小于触发阈值 |
| `MELONCLAW_AGENT_MODEL_CALL_LIMIT` | `30` | 每条用户消息、每个 Agent 的成功模型步骤上限 |
| `MELONCLAW_AGENT_TOOL_CALL_LIMIT` | `100` | 同一范围内的工具调用上限 |
| `MELONCLAW_AGENT_RETRY_MAX_RETRIES` | `2` | 瞬时模型错误最多额外重试次数，允许 0 |
| `MELONCLAW_AGENT_RETRY_INITIAL_DELAY` | `1` | 初始重试间隔，秒，允许 0 |
| `MELONCLAW_AGENT_RETRY_MAX_DELAY` | `10` | 最大重试间隔，秒，不小于初始间隔 |
| `MELONCLAW_AGENT_USAGE_ENABLED` | `true` | 启用用量观测 |
| `MELONCLAW_AGENT_TODO_ENABLED` | `true` | 启用任务清单；关闭后主／子 Agent 均不提供清单工具 |
| `MELONCLAW_AGENT_CACHE_ENTRIES` | `32` | Agent 实例缓存容量 |
| `MELONCLAW_TOOL_POOL_SIZE` | `16` | 每个会话业务工具池的容量，基础工具不占用此容量 |
| `MELONCLAW_TOOL_SELECTION_SIZE` | `8` | 每次工具选择数量上限，实际不超过池容量 |
| `MELONCLAW_TOOL_SELECTION_MAX_REQUESTS` | `4` | 每条用户消息最多处理的工具发现请求数，并行批次共享预算 |
| `MELONCLAW_TOOL_SELECTION_TIMEOUT_SECONDS` | `10` | 单次工具选择的超时秒数 |

摘要触发阈值加输出预留不能超过模型窗口。工具按需加载：已有能力足够时直接使用，缺少能力时由 `find_tools` 请求选择器扩充工具池；池跨消息复用，目录或权限变化后失效。选择器需要供应商支持 OpenAI 兼容结构化输出，失败或超时会保留旧池并提示降级，不执行候选工具，也不扩大权限。

主／子 Agent 独立计算调用预算，审批恢复不清零，因此这些限额**不是整个任务的全局费用上限**。已输出内容的模型请求不自动重试。回复完成后汇总主模型、选择器、摘要和子 Agent 已报告的 token；未上报的用量无法统计。详见 [运行控制](docs/design-docs/agent-runtime-controls.md)和[按需工具池](docs/design-docs/on-demand-tool-pool.md)。

### 附件配置

常用限制已列在 `.env.example`，大小单位为 MiB（1024 × 1024 字节）：

| 变量 | 默认值 | 含义 |
|---|---:|---|
| `MELONCLAW_ATTACHMENT_MAX_FILE_MB` | `20` | 单个附件大小上限 |
| `MELONCLAW_ATTACHMENT_MAX_PER_MESSAGE` | `10` | 每条消息附件数量上限 |
| `MELONCLAW_ATTACHMENT_MAX_TOTAL_MB` | `50` | 每条消息附件总大小上限 |
| `MELONCLAW_ATTACHMENT_PROJECT_MAX_MB` | `1024` | 每个会话／项目工作区的附件总容量上限 |
| `MELONCLAW_ATTACHMENT_IMAGE_MAX_EDGE` | `1568` | 发给模型的图片最长边像素，最小 64 |
| `MELONCLAW_ATTACHMENT_IMAGE_JPEG_QUALITY` | `85` | 出站 JPEG 压缩质量，实际限制在 1–95 |
| `MELONCLAW_ATTACHMENT_IMAGE_CACHE_ENTRIES` | `32` | 图片转换缓存数量 |

更细的解析、压缩包和超时参数见 [配置定义](src/melonclaw/core/config.py)，格式支持及校验边界见 [附件设计](docs/design-docs/chat-attachments.md)。图片需要当前模型具备系统明确声明的图片能力；文档需等待解析完成。扫描 PDF 不提供 OCR 保证，ZIP 用于 Skill 安装，不作为普通文档解析。

### MCP 配置：页面管理与种子文件

日常配置建议使用「拓展 → 连接器」：创建连接器，填写或粘贴 JSON，测试连接，核对工具白名单，保存后再启用。管理员页面创建的是共享服务，普通用户创建的是个人服务。支持 HTTP、SSE；stdio 只能由管理员配置，测试时会启动所填程序。

`mcp.json` 用于部署时补录共享种子。**不需要 MCP 时可不创建该文件，或使用 `{"mcpServers": {}}`。** 若参考 [mcp.json.example](mcp.json.example)，只保留实际需要且可连接的服务，不要直接启用全部示例。

下面是 HTTP 种子的结构示例，地址需替换成实际服务地址；凭据只通过环境变量引用：

```json
{
  "mcpServers": {
    "research": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${RESEARCH_MCP_TOKEN}"
      }
    }
  }
}
```

在本地 `.env` 中设置引用的 `RESEARCH_MCP_TOKEN`，再执行 `uv run melonclaw-db-init` 并重启。

| 字段 | 含义 |
|---|---|
| `mcpServers` 下的键 | 服务唯一名称，例如 `research` |
| `type` | `http`、`sse` 或 `stdio`；URL 未声明类型时按 HTTP，SSE 需明确指定 |
| `url` | HTTP/SSE 服务端点，不要把 Token 写进 URL |
| `headers` | HTTP 请求头；共享配置可用 `${ENV_NAME}` 引用部署凭据 |
| `command` / `args` | stdio 启动命令及参数数组，依赖需自行安装，不在参数中填写密钥 |
| `env` | stdio 子进程环境变量；共享配置可引用部署环境变量 |

初始化只补录缺失的全局项，新种子默认全员启用；已有连接和个人偏好不覆盖。运行时只读数据库，修改种子文件不会修改已保存连接。永久移除种子需同时移除文件条目和数据库中的配置。

连接器保存后默认停用，需明确启用。共享项的「对我关闭」只影响自己，「全员关闭」才影响所有用户。个人同名 MCP 会替代共享项，个人项停用或故障不回退；删除个人项后才可恢复共享项。白名单只允许勾选的工具，全部取消表示不允许任何工具；新增远端工具不会自动加入既有白名单。

测试连接只发现工具，不调用业务工具，但会向目标发送已配置凭据。MCP 工具调用均需人工审批，不进入 PTC。请求头和环境变量字面值以明文存入数据库，备份也须按含凭据数据保护；个人 MCP 不允许引用服务器环境变量。详见 [MCP 配置与权限](docs/design-docs/mcp-two-layer.md)。

## 功能使用

### 会话、项目与成果文件

普通会话有独立持久目录；项目内的会话共享项目工作区，适合围绕同一组资料持续工作。用户、项目、会话及附件归属由服务端校验，同租户用户也不能互相访问对方会话。

聊天顶部「文件」打开只读文件浏览器，可查看上传附件与生成文件，按会话／项目范围浏览、预览和下载。让助手将成果保存到工作区 `/outputs/` 并在回复中引用，就能从文件卡片直接打开；写入仍需审批。

HTML 预览支持不超过 2 MB 的 UTF-8 自包含页面和内嵌 CSS / JavaScript，不支持外部 CDN、相对资源或访问应用 API。Markdown、图片、PDF 和小型文本可预览，其他类型下载查看。文件显示当前内容，不保存历史快照，后续覆盖或删除会影响旧回复中的文件卡片。

表格可复制或下载 CSV，Mermaid 可导出 SVG，数据图表可展开绘图数据。图表须注明来源与单位，模拟数据须明确标注；来源展示不代表系统已独立核验网页或数值。更多说明见 [文件浏览器](docs/design-docs/workspace-file-browser.md)与[结果组件](docs/design-docs/chat-result-components.md)。

### 安装、更新与创建 Skill

在「拓展 → 技能」上传 ZIP 或从 GitHub 安装，预览正文、文件、来源及依赖后确认。ZIP 根目录应包含 `SKILL.md`，也可只包一层 Skill 目录；每次安装一个 Skill。远程安装支持 `owner/repo`、仓库 URL 或指向单个 Skill 的 `tree/<分支或commit>/<子目录>`，纯仓库默认 `main`，不支持名称含 `/` 的分支。

管理员安装为共享项，普通用户安装为个人项；安装流程中确认启用后，下一条消息可使用。管理页可调整个人启用状态，管理员可控制共享发布；更新从卡片菜单进入，预览正文差异和文件增删，不改变资源归属及个人偏好。草稿有效期 15 分钟；缺失或损坏资源可上传更新修复，管理员可用「恢复与检查」诊断未完成操作。

也可直接发消息：

- 上传 ZIP 后说：“帮我安装并启用这个 Skill。”
- 提供公开 GitHub Skill 链接并说：“帮我安装，先不要启用。”
- 说：“把刚才的工作流程整理成一个 Skill，确认后保存。”

聊天安装与生成都会先准备草稿，再由你审批。生成目前支持 `SKILL.md` 和 `references/` 下的 Markdown／文本，最多 32 个文件、总计 64,000 UTF-8 字节，不生成脚本或二进制内容。同名不会自动覆盖，更新使用管理页。安装不执行包内脚本，也不自动安装依赖；不要把密钥写进 Skill。

已配置可用模型后，还可问“怎么配置 Skill / MCP / 模型”，助手会按需读取内置教程。详见 [Skill 生命周期](docs/design-docs/skill-lifecycle.md)、[聊天安装](docs/design-docs/chat-skill-install.md)和[聊天生成](docs/design-docs/chat-skill-creation.md)。

### 在聊天中安装 MCP

粘贴完整 MCP JSON，并明确说“请安装到我名下”。支持 `mcpServers` 包装或单项连接，每条消息最多 8 个服务。助手展示个人安装清单，测试和安装分别审批；即使是管理员，通过聊天安装也只归本人。

聊天安装不支持个人 stdio、服务器环境变量引用或同名覆盖。配置由服务端转为有效期 24 小时的草稿，模型和聊天历史只接收草稿引用；凭据仅放在配置请求头中，不要放在块外说明、名称或 URL。安装并启用后下一条消息生效。详见 [聊天安装 MCP](docs/design-docs/chat-mcp-install.md)。

### 确认、补充信息与停止

「需要你允许这项操作」卡片展示对象、影响和参数，可允许、拒绝或按支持的操作改参；多项请求需逐项选择后统一提交。「需要你补充信息」只用于回答问题，跳过问题不会授权工具。

执行区按生成顺序展示实际工具进展、任务清单与等待阶段。模型每次返回一段连续的可读思考，页面就显示一个独立的「思考过程」条目；工具调用后的新思考会出现在该工具之后，完成后各段可分别展开。只有模型返回内容时才有新的正文或思考，计时和心跳不代表模型持续生成文字。点击「停止生成」保留已收到内容，但不会撤销已经完成的操作；结果待确认或断线时先「重新同步会话」，同步只读取状态，不重跑任务。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| 数据库连接失败 | 检查 PostgreSQL 是否启动、数据库是否存在、账号权限和 `DATABASE_URL` 驱动格式；不要公开实际连接串 |
| 提示表不存在或表结构不一致 | 空数据库首次运行 `uv run melonclaw-db-init`；要保留现有数据时执行 `uv run melonclaw-db-update`；启动服务不会自动建表或升级 |
| 前端无法启动或找不到 Vite | 运行 `npm --prefix frontend ci`；一键脚本不安装前端依赖 |
| 端口被占用 | 确认是否为本项目旧服务后停止，或按前文导出新的前后端端口 |
| 页面可打开但 API 不通 | 检查后端 `/api/status`、后端日志及 `MELONCLAW_API_TARGET`；改端口不能只改根目录 `.env` |
| 模型下拉为空 | 配置供应商凭据、添加并启用模型；初始化仅创建供应商模板 |
| 模型列表测试成功但聊天失败 | 核对准确模型名、聊天接口、工具调用支持、额度和上下文窗口；列表接口成功不代表聊天能力正常 |
| 提示工具选择降级 | 检查模型是否支持结构化输出及接口是否超时；已有工具池保留，可明确重提能力需求或换模型 |
| 提示“工具名称为空” | 本轮停止且该批工具未执行；切换模型或排查接口流式工具协议，此前已执行操作不会回滚 |
| 附件阻止发送 | 等待解析，失败时重新解析或移除；图片不被当前模型支持时换模型或移除图片 |
| MCP 不可用 | 检查已启用状态、服务类型、凭据引用和工具白名单，再主动测试；页面不自动检测全部连接 |
| Skill 未出现或安装失败 | 检查启用与共享状态、ZIP 层级、GitHub 子目录和草稿有效期；GitHub 限流时可改上传 ZIP |
| 上一轮未结束或连接中断 | 先重新同步；服务重启会将遗留运行标为失败，刷新后可继续；先核对工具结果再重发，避免重复操作 |

全局导航底部「系统状态」可查看当前用户的模型、工具、技能和 MCP 配置情况，其中 MCP 的启用状态不等于实时连接状态。

## 登录与用户／租户管理

登录页展示 MelonClaw Logo、用户 ID 和密码。表单关闭浏览器自动填充并使用独立字段名，避免同源页面曾保存的大模型 URL/API Key 被当作登录凭据回填。仅 `.env` 中 `profile=dev` 时额外显示“免密登录”，点击直接进入 admin；按钮不带开发后缀，不显示额外提示。配置变更后重启后端。普通用户创建时必须设置初始密码并再次输入确认（5～128 字符），密码只保存哈希。

仅 `profile=dev` 时，所有登录用户可通过左侧“更换用户”切换身份，包括 admin；切换后返回目标用户首页，原任务在后台继续，切回来可查询结果。刷新保留当前会话，左下角头像菜单“登出”撤销会话并回到登录页。未发送内容在切换前提示确认。

头像菜单提供“修改密码”：填写新密码和再次确认，不需要旧密码；成功后返回登录页。管理员也可在用户管理中修改用户密码，两次输入必须一致。

当前为 admin 时，左侧底部出现“系统管理”：

- 用户管理：搜索／筛选、创建、编辑名称和所属租户、修改密码、软删除。用户 ID 创建后不可修改或复用；删除保留个人数据。
- 租户管理：创建、搜索、修改名称、启停；租户 ID 不可修改，不提供删除。停用后成员不能登录或发起新业务请求，数据保留。
- 一个用户只属于一个租户。换租户后个人项目、会话、附件、配置和用户记忆保留，租户记忆留在原租户。存在运行中或等待审批／回答的任务时，先结束任务再改归属、删除用户或停用租户。
- 内置 admin 不可删除／转租户，system 不可停用。修改密码会撤销该用户相关会话，含其登录后切到别人的会话。忘记 admin 密码可在服务所在机器执行 `uv run melonclaw-admin-password`。

初始化只补录缺失账户，不覆盖已经设置的密码或编辑的资料。完整接口与边界见[用户管理与登录设计](docs/design-docs/user-management-login.md)。

## 安全与当前边界

- 页面提供密码登录；仅 `profile=dev` 时开放**全员可切换任意用户（含 admin）**的开发能力。非 dev 隐藏入口且后端拒绝免密登录及用户切换；当前应用仍定位于本机开发与个人使用。初始仅有 `admin`，其他用户由当前 admin 在系统管理中创建并选择租户。
- `LocalShellBackend` 不是沙箱，工作区目录也不是操作系统级隔离。当前仅适合本机 `127.0.0.1` 开发使用；面向共享或不可信用户前需要收紧身份切换权限、独立沙箱及凭据隔离。
- 文件写入、修改、删除、Shell、MCP 调用等通过人工审批；QuickJS 用于受限计算，不具备文件和网络权限。审批不能替代运行环境隔离，停止生成也不回滚副作用。
- 不提交 `.env`，不在 Skill、URL、命令参数、截图或排障输出中暴露凭据。数据库及其备份也可能包含模型和 MCP 凭据。
- 附件解析和 HTML 预览有类型、大小与访问限制，但不等同于恶意文件扫描或系统沙箱。不要让不可信用户直接使用宿主机执行能力。

## 开发与进一步阅读

```bash
scripts/check.sh              # 后端编译、测试、lint、锁文件、文档链接及前端检查
scripts/check.sh backend      # 仅后端相关检查
scripts/check.sh frontend     # 仅前端相关检查
```

前端依赖未安装时完整检查会跳过前端部分，请先运行 `npm --prefix frontend ci`。检查通过不代替真实数据库、模型 API 和 MCP 的连通验证。

- [知识库入口](docs/README.md)：文档导航。
- [架构与安全边界](docs/ARCHITECTURE.md)：模块职责、依赖方向与关键取舍。
- [前端开发与部署](docs/FRONTEND.md)：前端配置、构建与 UI 约定。
- [功能设计索引](docs/design-docs/index.md)：各项能力的数据流、权限和详细行为。
- [质量与已知差距](docs/QUALITY_SCORE.md)：测试覆盖、环境陷阱和待改进项。


### 全局导航

最左侧常驻图标栏提供 Home 和拓展。Home 返回原会话；未选择会话时显示新聊天页，不强制展开聊天侧栏。侧栏原有按钮负责收起，收起后可从聊天顶栏左上角展开；窄屏使用导航抽屉。

拓展隐藏聊天侧栏，提供技能、连接器、模型三个 TAB，并在当前浏览器记住上次选择。全局图标栏底部依次为更换用户、系统状态和当前用户头像；更换用户使用独立图标，点击最底部头像向右打开圆角菜单，顶部显示头像与全名，底部带图标的 Log out 仅为占位，不执行登出；模拟用户仍是开发身份，不是生产认证。

切换 Home／拓展保留当前聊天草稿、附件和正在进行的回复。返回时，原来位于底部则继续跟随回复，翻看历史时保持阅读位置并通过新消息入口跳转。切换用户仍清理旧聊天上下文。上述保留针对页面内切换，不表示刷新后恢复未发送草稿或附件。


全局侧边栏还提供 AMP 和 Mindera 两个入口作为页面占位。点击 A 打开 AMP 占位页，点击 M 打开 Mindera 占位页；当前不包含对应业务功能。


### 登录来源与公开就绪探测

Cookie 登录支持同源访问，以及 `MELONCLAW_ALLOWED_ORIGINS` 白名单中的同站跨域访问（例如同主机同协议不同端口）。Cookie 保持 `SameSite=Strict`，拒绝浏览器标记为跨站的写请求；不同站点或协议的部署应将 `/api` 反向代理到前端同源地址。`localhost` 与 `127.0.0.1` 不应混用。

无需登录的 `GET /api/ready` 只返回 `{"ready": true/false}`：服务初始化完成返回 200，初始化中或失败返回 503，响应禁止缓存。`scripts/start.sh` 使用它等待后端就绪；`/api/auth/config` 只用于登录页配置，不代表就绪。已登录的 `/api/status` 提供详细状态。正常启动命令仍为 `scripts/start.sh --profile dev`；本次修复无数据库结构变化，无需运行 db-init 或 db-update。

## Linux 服务器部署与升级

完整步骤见 [服务器部署目录](docs/deployment.md)，可安装的配置模板位于 `ops/`。代码固定放在 `/opt/melonclaw/`，在 `/opt` 下 clone 后原地更新，不使用软链接；数据 `/var/lib/melonclaw/data`，工作区 `/var/lib/melonclaw/workspaces`；外部配置 `/etc/melonclaw/melonclaw.env`；日志 `/var/log/melonclaw`；临时运行目录 `/run/melonclaw`。PostgreSQL 使用独立持久存储。容器必须挂载数据目录和数据库持久卷，不能依赖容器内部文件系统。

`MELONCLAW_ENV_FILE` 指定配置文件；持久数据根与工作区禁止配置在当前代码目录内。服务器用 systemd 管理后端、Nginx 提供前端构建产物和同源 `/api`，不使用 Vite 开发服务器作为正式前端。升级只替换代码、安装依赖、按需执行 `melonclaw-db-update` 并重启；不清库、不覆盖用户 Skill、不删除持久目录。首次上线才执行 `melonclaw-db-init`。
