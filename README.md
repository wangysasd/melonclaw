# MelonClaw

MelonClaw 是基于 Deep Agents 的通用 AI 助手，在浏览器中完成研究、写作、文件处理和多步骤任务。它整合模型、工具、Skill、MCP、项目工作区与长期记忆，支持查看执行过程，并在敏感操作前由用户确认。

当 `brand` 不为 `rms` 时，首次访问须点击「我已确认并进入」阅读确认个人学习用途、投资风险及禁止外传提示；确认状态保存在当前浏览器的本地存储，清除站点数据后会再次提示。首页研究服务卡片下方展示完整「本站合规声明」。该提示不替代登录与服务端权限控制。

## 本地快速启动

### 1. 准备环境

- Python 3.11+（低于 4.0）与 [uv](https://docs.astral.sh/uv/)。
- Node.js 20+ 与 npm。
- PostgreSQL 14+：已启动，应用账号可连接数据库并建表。
- macOS / Linux（Windows 可用 WSL）；一键脚本需要 Bash、`curl`、`lsof`。

在项目根目录执行：

```bash
# 已有 .env 时保留原文件，不要覆盖
cp .env.example .env
uv sync --locked
npm --prefix frontend ci
```

### 2. 填写必填环境变量

编辑根目录 `.env`，**首次启动唯一必填项是 `DATABASE_URL`**：

```dotenv
DATABASE_URL=postgresql+asyncpg://<用户>:<密码>@<主机>:<端口>/<数据库名>
```

将尖括号占位符替换为自己的 PostgreSQL 连接信息；用户名或密码中的 URL 特殊字符需百分号编码。数据库必须提前创建，例如本地 PostgreSQL 认证配置完成后执行 `createdb melonclaw`。

其他变量均可保持模板默认值：

| 可选变量 | 用途与默认值 |
|---|---|
| `TAVILY_API_KEY` | 联网搜索凭据；留空不影响启动，相关搜索能力不可用 |
| `MELONCLAW_DATA_DIR` | Skill 等持久资源目录，默认 `~/.melonclaw/data` |
| `MELONCLAW_WORKSPACE_DIR` | 会话与项目文件目录，默认 `~/.melonclaw/workspaces` |
| `profile` | 仅 `dev` 开启免密登录和全员身份切换；直接启动后端时按此值生效 |

持久目录必须位于项目代码目录之外。不要提交 `.env` 或公开实际连接串、Key、Token。后端默认读取根目录 `.env`，已导出的同名环境变量优先；更多可选参数见 [.env.example](.env.example)。

**模型名称、服务地址和 API Key 在启动后的页面配置**，不通过环境变量自动创建模型。首次启动无需配置 Tavily 或 MCP。

### 3. 首次初始化

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

以上命令安装缺失的内置 Skill 并初始化数据库，创建管理员 `admin`（初始密码 `admin`）和未启用、无凭据的供应商模板。初始化不会创建默认模型，也不会自动清库或覆盖已有 Skill。无需 MCP 时可不创建 `mcp.json`。

### 4. 启动前后端

一键启动：

```bash
scripts/start.sh
```

访问前端 [http://127.0.0.1:8001](http://127.0.0.1:8001)，后端默认地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。一键脚本默认使用 `dev`，读取 `.env` 并开启开发身份切换，仅用于本机开发。

也可以在两个终端分别启动，方便查看输出：

```bash
# 终端一：后端
uv run melonclaw-web
```

```bash
# 终端二：前端
npm --prefix frontend run dev
```

用 `admin` / `admin` 登录后修改密码，在「拓展 → 模型」配置供应商地址和 Key、启用供应商，再添加并启用支持工具调用的模型。选择模型并发送消息，收到回复即完成基本验证；未配置模型时可以管理配置，但不能聊天。

## 停止、更新与排障

```bash
scripts/shutdown.sh           # 停止一键脚本启动的前后端
scripts/restart.sh            # 重启前后端
```

更新代码后重新运行 `uv sync --locked` 和 `npm --prefix frontend ci`。已有数据库需要升级时，停服后执行 `uv run melonclaw-db-update`，再重启；该命令保留业务数据，首次建库才用 `melonclaw-db-init`。服务启动不会自动建表或升级。

- 数据库连接失败：检查 PostgreSQL、数据库名称、账号权限和连接串格式。
- 页面打开但 API 不通：检查后端日志及 [就绪探测](http://127.0.0.1:8000/api/ready)；一键脚本日志默认在 `${TMPDIR:-/tmp}/melonclaw-dev/`。
- 端口被占用：确认占用进程，若是本项目旧服务先停止。自定义端口需在启动终端导出 `MELONCLAW_PORT`、`MELONCLAW_FRONTEND_PORT`，停止和重启时保持一致；脚本与前端代理不会自动从根目录 `.env` 读取端口参数。
- 模型列表为空：在页面添加并启用模型；模型列表测试成功后，仍需通过实际聊天验证。

当前适合本机开发与个人使用。`LocalShellBackend` 不是安全沙箱，人工审批不能替代系统隔离；不要向不可信用户开放宿主机执行能力。

## 更多文档

- [文档导航](docs/README.md)：功能说明与设计文档入口。
- [前端开发](docs/FRONTEND.md)：配置、代理与构建。
- [架构说明](docs/ARCHITECTURE.md)：模块边界与安全设计。
- [服务器部署](docs/deployment.md) · [首次上线](docs/first-deployment.md) · [重新发版](ops/DEPLOY.md)。

开发验证：`scripts/check.sh`（后端与前端检查；未安装前端依赖时跳过前端部分）。

常规发版（无需数据库升级或部署配置变更）：以 ubuntu 在服务器执行 `cd /opt/melonclaw && bash ops/deploy.sh`。脚本先停后端，再拉代码、同步依赖、构建前端、启动并检查就绪；停服后失败会保持后端停止。详见 [发版手册](ops/DEPLOY.md#常规发版一条命令)。
