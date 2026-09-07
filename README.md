# MelonClaw

<p align="center">
  <img src="src/melonclaw/web/static/assets/brand/melon-claw.png" alt="MelonClaw" width="314" height="314" />
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

## 环境要求

- Python 3.11+
- `uv`
- PostgreSQL 14+
- 一个模型 API，默认使用 DeepSeek，也支持 OpenAI 兼容接口
- Tavily API Key（仅在需要联网搜索时使用）

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
uv run melonclaw-web
```

打开 <http://127.0.0.1:8000>，即可开始使用。监听地址和端口可以通过 `MELONCLAW_HOST`、`MELONCLAW_PORT` 修改。

## 如何使用

1. 在页面中选择开发用模拟用户，创建或选择一个 Project。
2. 新建会话并直接描述任务，例如：

   ```text
   请比较 LangGraph 当前的持久化方案，并给出官方文档依据。
   ```

3. Agent 会根据任务决定是否搜索资料、读取项目文件、调用 MCP、委派子 Agent 或使用 Interpreter。
4. 如果涉及写文件、编辑文件、删除文件或执行 Shell，检查审批卡片中的参数后选择批准、编辑或拒绝。

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
| `DATABASE_URL` | PostgreSQL 连接，使用 `postgresql+asyncpg://` |
| `MELONCLAW_WORKSPACE_DIR` | Project 工作区根目录 |
| `MELONCLAW_HOST` / `MELONCLAW_PORT` | Web 监听地址和端口 |

MCP 服务定义放在根目录的 `mcp.json` 中。没有启用 MCP 时应用仍可正常启动；启用外部服务时，再按服务要求配置对应的 Token 和服务名环境变量。

切换到 OpenAI 兼容接口时，将 `DEEPAGENTS_PROVIDER` 设为 `openai`，并填写对应的 `OPENAI_*` 配置。

## 使用边界

- `.env` 中的 API Key、Token 和数据库密码只保存在本地，不要提交到 Git。
- 页面中的用户和租户是开发入口，不代表生产环境的身份认证和授权。
- `LocalShellBackend` 不提供操作系统级隔离；不要在不受信任或未完成认证的环境中直接开放服务。
- 联网搜索、MCP 和模型调用依赖相应的外部服务及凭据；未配置时，其他不依赖它们的能力仍可使用。
