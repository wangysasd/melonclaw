<p align="center">
  <img src="frontend/public/assets/brand/melonclaw-readme.png" alt="MelonClaw Logo" width="360">
</p>

# MelonClaw

MelonClaw 是基于 Deep Agents 的通用 AI 助手，把对话、工具、文件和多步骤任务放在同一个 Web 界面中。你可以用它研究资料、撰写报告、处理文件，也可以通过 Skill 和 MCP 接入自己的工作方法与外部服务。

## 功能与特色

- **多步骤任务**：围绕目标规划和执行，调用工具或子 Agent，并在聊天中查看进展、工具结果和生成文件。
- **资料与文件处理**：上传附件供助手分析，生成可预览、下载的文件；项目下的多个会话共享工作区。
- **Skill 与 MCP 扩展**：用 Skill 复用工作流程，用 MCP 连接外部数据和服务，支持系统共享与个人配置。
- **模型自主配置**：在页面管理供应商、模型和凭据，支持全局内置模型与个人模型。
- **长期记忆**：按系统、租户和用户范围保留信息，帮助跨会话延续工作。
- **定时任务**：通过对话或表单设置一次性、每天、每周任务，结果持续写入固定会话，可继续追问。
- **过程可观察**：展示执行过程，对需要审批的操作进行确认；可选接入 Langfuse 查看调用链、用量和异常。

首页提供权益、固收研究提示词，点击后可补充研究对象和日期再发送。具体取数能力取决于已安装的 Skill、启用的 MCP 和数据订阅权限。

## 本地快速启动

### 准备环境

- Python 3.11+（低于 4.0）与 [uv](https://docs.astral.sh/uv/)。
- Node.js 20+ 与 npm。
- 已启动的 PostgreSQL 14+，以及可连接数据库、创建表的应用账号。
- macOS / Linux；Windows 可使用 WSL。一键启动脚本需要 Bash、`curl`、`lsof`。

以下命令均在项目根目录执行。

### 安装依赖

```bash
uv sync --locked
npm --prefix frontend ci

# 仅在没有 .env 时复制，保留已有配置
if [ ! -f .env ]; then cp .env.example .env; fi
```

### 配置数据库

提前创建 PostgreSQL 数据库，并在 `.env` 中填写连接信息：

```dotenv
DATABASE_URL=postgresql+asyncpg://<用户>:<密码>@<主机>:<端口>/<数据库名>
```

首次启动唯一必填配置是 `DATABASE_URL`，其他项可保持模板值。用户名或密码中的 URL 特殊字符需要百分号编码；不要提交 `.env` 或公开实际连接串。

模型在启动后的页面配置，无需提前填写模型 Key。持久资源与工作区默认保存在 `~/.melonclaw/` 下，不要将它们配置到代码目录内。更多选项见[配置说明](docs/configuration.md)。

### 安装内置资源并初始化

仅在首次使用的数据库上执行：

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

资源命令只安装缺失的内置 Skill，不覆盖已有同名资源。初始化创建当前数据库结构、基础配置和管理员 `admin`，初始密码为 `admin`；不会自动清库，也不会创建可用模型。

已有部署请按下方[更新项目](#更新项目)操作。服务启动不会自动建表或升级。

### 启动服务

```bash
scripts/start.sh
```

打开 [http://127.0.0.1:8001](http://127.0.0.1:8001)。后端默认地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

一键脚本默认使用 `dev`、读取 `.env`，并开启开发身份切换，仅用于本机开发。需要分别启动前后端时，见[手动启动](docs/configuration.md#手动启动)；服务器部署见[Linux 部署说明](docs/deployment.md)。

### 配置模型，发送第一条消息

1. 使用 `admin` / `admin` 登录，并修改初始密码。
2. 打开「拓展 → 模型」，配置供应商地址和 API Key，启用供应商，再添加并启用支持工具调用的模型。
3. 新建对话，选择模型，发送“介绍一下你能做什么”。收到回复后，基本链路即已打通。
4. 尝试上传一份资料，发送“总结这份资料，并生成一份 Markdown 文件供我下载”。

没有可用模型时可以管理配置，但不能聊天。初始化不会从环境变量生成模型或选择默认模型。

## 按需扩展

| 想做什么 | 如何配置 |
|---|---|
| 搜索公开网页、读取网页正文 | 在 `.env` 设置 `DEEPSEEK_API_KEY` 后重启；该工具的 Key 独立于聊天模型配置 |
| 使用专业工作流程 | 在「拓展 → 技能」管理 Skill；脚本依赖与外部客户端需另行准备 |
| 连接外部数据或服务 | 在「拓展 → 连接器」配置 MCP，启用服务并限制工具白名单 |
| 给 Skill 脚本提供业务凭据 | 在「拓展 → 环境变量」配置，管理员维护共享项，普通用户维护个人项 |
| 定期执行任务 | 在「定时任务」创建，或在普通会话直接描述执行要求与时间 |
| 查看模型调用与用量 | 按[Langfuse 配置](docs/configuration.md#langfuse-追踪)开启监控 |

启用的个人环境变量覆盖同名共享值；个人停用或删除后回退共享值，保存后下一轮消息生效。业务变量只从数据库注入 Skill/Shell，不再使用 `.env` 的旧默认凭据名单或 `MELONCLAW_SHELL_ENV_ALLOWLIST`。全局 MCP 配置中的 `${VAR}` 仍从服务进程环境解析，数据库环境变量资源不参与该替换；也可直接在 MCP 配置中保存连接凭据。详见[配置说明](docs/configuration.md)与[环境变量设计](docs/design-docs/environment-and-preparation.md)。

定时任务支持手动、指定时间一次、每天或每周，统一使用北京时间。聊天新建任务未指定模型时，使用管理员设置的系统默认模型；需要先确保默认模型可用。任务在固定普通会话中累计结果，关闭页面后仍会执行，但 Web 服务必须保持运行。错过计划不补跑，上一轮未结束时跳过本次；暂停只停止后续计划。后台任务目前只提供文字生成、公开搜索、受限子任务和本任务历史，不使用项目、Skill、MCP、Memory 或 Shell。更多说明见[任务中心设计](docs/design-docs/task-center.md)。

## 停止与更新

停止或重启本地一键脚本启动的服务：

```bash
scripts/shutdown.sh
scripts/restart.sh
```

### 更新项目

先等待活动任务结束，停止服务并备份数据库、数据目录、工作区和配置，再更新代码与依赖：

```bash
scripts/shutdown.sh
git pull --ff-only
uv sync --locked
npm --prefix frontend ci
uv run melonclaw-db-update
scripts/start.sh
```

`melonclaw-db-update` 保留业务数据，只执行尚未完成的升级步骤；不要用 `melonclaw-db-init` 代替升级。手工修改数据库或 Skill 文件后需要重启 Web。Linux 标准部署使用外部配置文件、前端构建和 systemd 服务，按[部署文档](docs/deployment.md)执行。

## 常见问题与使用边界

- **数据库连接失败**：确认 PostgreSQL 已启动、数据库已创建、账号有建表权限，检查 `DATABASE_URL` 格式及特殊字符编码。
- **页面提示没有模型**：到「拓展 → 模型」检查供应商与模型是否启用、Key 是否有效、模型是否支持工具调用。
- **网页搜索或取数失败**：检查相应工具凭据、连接器启用状态、白名单和订阅权限；缺少某项扩展不影响基本聊天。
- **服务未就绪**：查看启动日志与 `/api/ready`。仅支持单实例、单 Web 进程；不能增加多个 workers 或重叠启动新旧服务，定时任务执行器会拒绝第二个实例。
- **执行权限**：当前 `LocalShellBackend` 不是安全沙箱，适合本机开发与受信任使用。文件写入、编辑、删除及 Shell 不逐次审批；已启用且白名单允许的 MCP 工具直接调用。Skill 安装、MCP 测试与安装等操作仍需审批。不要向不可信用户开放宿主机执行能力。
- **凭据与数据**：数据库及备份可能包含业务凭据，请妥善保护。开启 Langfuse 内容采集会发送脱敏后的对话与工具文本，启用前确认业务内容允许外发；可关闭内容采集或整个监控。

`dev` 的身份切换是开发功能，不能作为生产认证与用户隔离方案。部署限制、安全边界和排障细节见[配置说明](docs/configuration.md)及[部署说明](docs/deployment.md)。

## 开发与文档

后端采用 Python、Deep Agents 与 FastAPI，前端采用 React、TypeScript 与 Vite，持久化使用 PostgreSQL。

- [文档导航](docs/README.md)：配置、架构、前端与质量说明。
- [架构说明](docs/ARCHITECTURE.md)：模块边界、依赖方向与设计取舍。
- [前端开发](docs/FRONTEND.md)：本地开发、构建与 UI 约定。
- [设计文档](docs/design-docs/index.md)：各项能力的方案与验证记录。

提交前在根目录运行：

```bash
scripts/check.sh
```

检查包括后端编译、测试、lint，前端 lint、类型检查、测试，以及锁文件、文档链接与空白错误检查；未安装前端依赖时跳过前端部分。
