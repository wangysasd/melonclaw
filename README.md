# MelonClaw

<p align="center">
  <img src="frontend/public/assets/brand/melon-claw.png" alt="MelonClaw" width="314" height="314" />
</p>

MelonClaw 是一个基于 Deep Agents 的通用 AI 助手。它通过 Web 界面处理问答、写作、研究、信息整理和计划制定等任务，并可按需使用联网搜索、文件工作区、MCP 工具、子 Agent 和 JavaScript 计算能力。

项目默认运行在本机或受信任环境中。Web 入口当前使用开发模拟用户，没有生产级登录鉴权；文件写入和 Shell 执行会经过人工审批，但 `LocalShellBackend` 不是安全沙箱。

## 项目特点

- **通用任务处理**：支持问答、总结、翻译、改写、分析、研究和计划制定。
- **按需扩展工具**：根据任务使用 Tavily 搜索、项目文件、MCP 服务或 QuickJS Interpreter。
- **可控的副作用**：写文件、修改文件、删除文件和执行 Shell 命令前需要人工批准、编辑参数或拒绝。
- **多会话工作区**：Project 下的会话共享持久文件目录，但对话状态、审批状态和 Interpreter 状态彼此隔离。
- **长期记忆与恢复**：使用 PostgreSQL 保存业务数据、对话 Checkpoint 和 Global/Tenant/User Memory。
- **子 Agent 协作**：相对独立的工作可以委派给通用子 Agent，再由主 Agent 汇总结果。
- **流式反馈**：Web 页面实时展示回答、工具调用、工具结果、子 Agent 状态和审批过程。
- **可读交互界面**：正文、侧栏、工具卡片和输入区采用统一的放大字号，方便持续阅读和操作。
- **可收起侧栏**：桌面端侧栏可收起为仅保留 MelonClaw 标志和展开按钮的窄条，点击展开按钮即可恢复项目与会话导航。

## 环境要求

- Python 3.11+
- `uv`
- PostgreSQL 14+
- 一个模型 API，默认使用 DeepSeek，也支持 OpenAI 兼容接口
- Tavily API Key（仅在需要联网搜索时使用）
- Node.js 20+ 与 npm（仅在开发或构建 `frontend/` 独立前端时需要）

## 快速开始

### 1. 获取代码并配置环境

```bash
git clone <your-repository-url>
cd melonclaw
cp .env.example .env
```

在 `.env` 中填写模型和数据库配置。默认使用 DeepSeek：

```dotenv
DEEPAGENTS_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=你的 DeepSeek 模型名

DATABASE_URL=postgresql+asyncpg://用户名:密码@127.0.0.1:5432/melonclaw
TAVILY_API_KEY=你的 Tavily API Key
```

数据库需要提前创建；初始化命令只负责创建或升级应用表。

### 2. 安装依赖并初始化数据库

```bash
uv sync --locked
uv run melonclaw-db-init
```

### 3. 启动应用

```bash
scripts/start.sh
```

打开 <http://127.0.0.1:8001>，即可开始使用。启动脚本会同时运行 React/Vite 前端和 FastAPI 后端；默认绑定本机回环地址，监听地址和端口可以通过 `MELONCLAW_HOST`、`MELONCLAW_PORT`、`MELONCLAW_FRONTEND_HOST`、`MELONCLAW_FRONTEND_PORT` 修改。脚本会先检查两个端口，任一端口被占用时不会启动任何服务，请先执行 `scripts/shutdown.sh`。脚本还会在系统临时目录下为 `uv` 创建可写缓存，必要时可用 `UV_CACHE_DIR` 覆盖。

## 前端独立项目（frontend/）

前端唯一实现位于根目录的 `frontend/`，是一个独立的 React + Vite + TypeScript 项目，与 Python/uv 构建体系解耦。它已接入完整聊天界面（SSE 消息流、Markdown 渲染、工具时间线与子 Agent 卡片、HITL 审批面板）；FastAPI 仅提供 `/api` 接口，不再托管旧版静态页面。

开发模式（前后端联调）：

```bash
# 一键启动（后端 FastAPI + 前端 Vite，端口被占用时会先报错）
scripts/start.sh

# 一键停止（日志保留在系统临时目录的 melonclaw-dev/ 下）
scripts/shutdown.sh
```

也可以手动分步启动：

```bash
uv run melonclaw-web        # 后端 API，默认 127.0.0.1:8000
cd frontend
npm install
npm run dev -- --host 127.0.0.1  # Vite dev server，默认 http://127.0.0.1:8001
```

打开 <http://127.0.0.1:8001>。Vite 会把 `/api` 请求代理到后端，默认目标 `http://127.0.0.1:8000`，可用 `MELONCLAW_PORT`（改后端端口）或 `MELONCLAW_API_TARGET`（改完整地址）覆盖；SSE 流式响应在代理层关闭缓冲。若手动启动 Vite，建议显式绑定回环地址：`npm run dev -- --host 127.0.0.1`。

生产构建与部署：

```bash
cd frontend
npm run build               # 类型检查 + 构建产物输出到 frontend/dist/
```

`dist/` 由独立的 Node 静态服务或 Nginx 托管，后端只提供 API。两种接法：

- **同源反代（推荐）**：静态服务把 `/api` 反向代理到 FastAPI，无需跨域。Nginx 示例：

  ```nginx
  server {
    listen 8080;
    root /path/to/frontend/dist;

    location /api/ {
      proxy_pass http://127.0.0.1:8000;
      proxy_buffering off;              # SSE 必须关闭缓冲
      proxy_set_header Host $host;
      proxy_read_timeout 3600s;
    }

    location / {
      try_files $uri /index.html;
    }
  }
  ```

- **跨域直连**：构建前设置 `VITE_API_BASE_URL`（如 `https://api.example.com`），并在后端用 `MELONCLAW_ALLOWED_ORIGINS` 放行前端来源（逗号分隔 origin，如 `https://web.example.com`；未配置时默认放行 `http://localhost:8001` 与 `http://127.0.0.1:8001`）。

其他常用命令：`npm run typecheck`、`npm run lint`、`npm run test`、`npm run preview`。

## 如何使用

1. 在页面中选择开发用模拟用户，创建或选择一个 Project。
2. 新建会话并直接描述任务，例如：

   ```text
   请比较 LangGraph 当前的持久化方案，并给出官方文档依据。
   ```

3. Agent 会根据任务决定是否搜索资料、读取项目文件、调用 MCP、委派子 Agent 或使用 Interpreter。
4. 如果涉及写文件、编辑文件、删除文件或执行 Shell，审批卡片会展开显示本次操作的工具和参数。逐项选择“允许本次”“编辑参数”或“拒绝”后，点击提交；没有明确选择时不能提交。审批暂停期间可以继续在输入框中预写下一条消息，但必须先完成当前审批。

聊天页面会把工具活动与最终回答分层显示：成功的工具默认收起，失败或没有收到终态的工具会展开并标记原因；流式连接异常时会保留已收到的回答，并提供“重新同步会话”入口。切换会话或刷新页面后，待审批状态会从服务端恢复。

桌面端侧栏可通过顶部收起按钮变为窄条；收起后只显示品牌标志和恢复按钮，不影响当前会话。移动端继续通过顶部菜单打开抽屉式导航。

常见使用方式：

- **研究与联网搜索**：配置 `TAVILY_API_KEY` 后，直接提出需要实时资料或来源核验的问题。
- **文件处理**：文件写入当前 Project 的持久工作区，不会默认写入仓库源码；在 Agent 中使用类似 `/summary.md` 的工作区路径。
- **数据计算**：要求 Agent 使用 `eval` 完成循环、排序、聚合等纯计算。Interpreter 没有文件、网络和 Shell 权限。
- **项目隔离**：同一 Project 的会话共享文件工作区，消息和运行状态仍分别保存。工作区根目录默认位于 `~/.melonclaw/workspaces`，每个 Project 使用其下的 `projects/<project_id>` 子目录，可用 `MELONCLAW_WORKSPACE_DIR` 修改；仓库根目录不再创建运行时 `temp/`。

## 可选配置

| 变量 | 用途 |
| --- | --- |
| `DEEPAGENTS_PROVIDER` | 模型 provider，默认是 `deepseek` |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | DeepSeek 或其他兼容接口的配置 |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | 切换到 OpenAI 兼容接口时使用 |
| `TAVILY_API_KEY` | 启用联网搜索 |
| `TUSHARE_MCP_TOKEN` / `YUJIAN_MCP_TOKEN` | 仅当 `mcp.json` 中对应服务写出占位符时，用于展开 MCP 凭据 |
| `DATABASE_URL` | PostgreSQL 连接，使用 `postgresql+asyncpg://` |
| `MELONCLAW_WORKSPACE_DIR` | Project 工作区根目录 |
| `MELONCLAW_HOST` / `MELONCLAW_PORT` | Web 监听地址和端口 |
| `MELONCLAW_FRONTEND_HOST` / `MELONCLAW_FRONTEND_PORT` | React/Vite 开发服务器监听地址和端口，默认 `127.0.0.1:8001` |
| `UV_CACHE_DIR` | `uv` 缓存目录；启动脚本默认使用系统临时目录下的项目缓存 |
| `MELONCLAW_ALLOWED_ORIGINS` | 独立前端跨域部署时放行的 origin（逗号分隔；未配置时默认放行本地 Vite 开发端口） |

MCP 服务定义放在根目录的 `mcp.json` 中，文件必须是合法 JSON；应用默认加载其中列出的全部服务。`.env` 不会因为多出某个 Token 就自动启用服务，只用于替换 `mcp.json` 明确写出的 `${VARIABLE_NAME}` 占位符；占位符对应变量缺失时，后端会报告具体变量名。暂时不用 MCP 时可将配置设为 `{}`，或将整份文件全部用 `//` 注释。

切换到 OpenAI 兼容接口时，将 `DEEPAGENTS_PROVIDER` 设为 `openai`，并填写对应的 `OPENAI_*` 配置。

## 使用边界

- `.env` 中的 API Key、Token 和数据库密码只保存在本地，不要提交到 Git。
- 页面中的用户和租户是开发入口，不代表生产环境的身份认证和授权。
- `LocalShellBackend` 不提供操作系统级隔离；不要在不受信任或未完成认证的环境中直接开放服务。
- 联网搜索、MCP 和模型调用依赖相应的外部服务及凭据；未配置时，其他不依赖它们的能力仍可使用。
