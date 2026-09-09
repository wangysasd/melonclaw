# MelonClaw

<p align="center">
  <img src="frontend/public/assets/brand/melon-claw.png" alt="MelonClaw" width="314" height="314" />
</p>

MelonClaw 是一个基于 Deep Agents 的通用 AI 助手。通过 Web 界面处理问答、写作、研究和计划制定等任务，并可按需使用联网搜索、文件工作区、MCP 工具、子 Agent 和 JavaScript 计算能力。

## ✨ 项目特色

- 🧠 **通用任务处理** — 问答、总结、翻译、分析、研究和计划制定
- 🔍 **联网搜索** — 配置 Tavily 后即可获取实时资料并核验来源
- 📁 **多会话工作区** — Project 下的会话共享持久文件目录，对话状态彼此隔离
- 🤝 **子 Agent 协作** — 独立工作委派给子 Agent，主 Agent 汇总结果
- 🧮 **安全计算** — QuickJS Interpreter 完成纯计算，无文件、网络和 Shell 权限
- ✅ **可控副作用** — 写文件、删文件和执行 Shell 前需人工批准、编辑参数或拒绝
- 💾 **长期记忆与恢复** — PostgreSQL 保存业务数据、对话 Checkpoint 和多级 Memory
- ⚡ **流式反馈** — 实时展示回答、工具调用、子 Agent 状态和审批过程

## 📋 环境要求

- Python 3.11+、`uv`、PostgreSQL 14+
- 一个模型 API（默认 DeepSeek，支持 OpenAI 兼容接口）
- Tavily API Key（可选，联网搜索用）
- Node.js 20+ 与 npm（可选，独立开发前端时用）

## 🚀 快速开始

### 1️⃣ 获取代码并配置

```bash
git clone <your-repository-url>
cd melonclaw
cp .env.example .env
```

在 `.env` 中填写模型和数据库配置（数据库需提前创建）：

```dotenv
DEEPAGENTS_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=你的 DeepSeek 模型名

DATABASE_URL=postgresql+asyncpg://用户名:密码@127.0.0.1:5432/melonclaw
TAVILY_API_KEY=你的 Tavily API Key
```

### 2️⃣ 安装依赖并初始化数据库

```bash
uv sync --locked
uv run melonclaw-db-init
```

### 3️⃣ 启动 / 重启 / 停止

```bash
scripts/start.sh      # 一键启动（前端 Vite + 后端 FastAPI）
scripts/restart.sh    # 按当前状态自动启动或停止后重启
scripts/shutdown.sh   # 一键停止
```

打开 <http://127.0.0.1:8001> 即可使用。端口被占用时脚本不会启动任何服务，请先执行 `scripts/shutdown.sh`。

> 💡 后端需在 IDE 中手工调试时，可加 `frontend` 参数只操作前端：`scripts/start.sh frontend`。监听地址和端口可通过 `MELONCLAW_HOST`、`MELONCLAW_PORT`、`MELONCLAW_FRONTEND_HOST`、`MELONCLAW_FRONTEND_PORT` 修改。

## 💬 如何使用

1. 选择开发用模拟用户，创建或选择一个 Project，新建会话后直接描述任务。
2. Agent 会自动决定是否搜索资料、读写项目文件、调用 MCP、委派子 Agent 或使用 Interpreter。
3. 涉及写文件或执行 Shell 时，审批卡片会展示工具和参数，逐项选择「允许本次」「编辑参数」或「拒绝」后提交。

常用玩法：

- 🔎 **联网研究** — 直接提出需要实时资料或来源核验的问题
- 📝 **文件处理** — 在 Agent 中使用类似 `/summary.md` 的工作区路径，文件写入 Project 持久工作区（默认 `~/.melonclaw/workspaces`）
- 📊 **数据计算** — 让 Agent 用 `eval` 完成循环、排序、聚合等纯计算

## ⚙️ 可选配置

| 变量 | 用途 |
| --- | --- |
| `DEEPAGENTS_PROVIDER` | 模型 provider，默认 `deepseek`，可切 `openai` |
| `DEEPSEEK_*` / `OPENAI_*` | 模型接口配置 |
| `TAVILY_API_KEY` | 启用联网搜索 |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `MELONCLAW_WORKSPACE_DIR` | Project 工作区根目录 |
| `MELONCLAW_HOST` / `MELONCLAW_PORT` | 后端监听地址和端口 |
| `MELONCLAW_FRONTEND_HOST` / `MELONCLAW_FRONTEND_PORT` | 前端开发服务器监听地址和端口 |
| `MELONCLAW_ALLOWED_ORIGINS` | 独立前端跨域部署时放行的 origin |

MCP 服务定义放在根目录 `mcp.json`（可为 `{}` 留空）；`.env` 中的 Token 只用于替换其中 `${VARIABLE_NAME}` 占位符。

## 🖥️ 前端独立开发与部署

前端位于 `frontend/`，是独立的 React + Vite + TypeScript 项目：

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1   # 开发模式，Vite 会把 /api 代理到后端
npm run build                     # 构建产物输出到 frontend/dist/
```

生产部署时由 Nginx 或 Node 静态服务托管 `dist/`，并将 `/api` 反向代理到 FastAPI（SSE 需关闭缓冲）；跨域直连时用 `VITE_API_BASE_URL` 指定后端地址。

## ⚠️ 使用边界

- `.env` 中的 API Key、Token 和数据库密码只保存在本地，不要提交到 Git。
- 页面中的用户和租户是开发入口，不代表生产环境的身份认证。
- `LocalShellBackend` 不是安全沙箱，不要在不受信任的环境中直接开放服务。
- 联网搜索、MCP 和模型调用依赖相应外部服务；未配置时其他能力仍可使用。
