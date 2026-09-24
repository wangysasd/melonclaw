# MelonClaw

<p align="center">
  <img src="frontend/public/assets/brand/melon-claw.png" alt="MelonClaw" width="314" height="314" />
</p>

基于 Deep Agents 的通用 AI 助手：在 Web 界面完成问答、写作、研究和计划制定，所有副作用默认受控。

## ✨ 为什么用 MelonClaw

- ✅ **可控副作用**：写文件、删文件、Shell 执行前需批准，可改参或拒绝。
- 🙋 **主动提问**：关键信息缺失时暂停并给出结构化选项，提交后继续。
- 📁 **工作区隔离**：普通会话各用独立目录，Project 内会话共享项目目录。
- 🤝 **协作与计算**：子 Agent 委派独立任务；纯计算走 QuickJS，无文件网络权限。
- 💾 **长期记忆**：PostgreSQL 保存业务数据、对话 Checkpoint 和多级 Memory。
- 🎛️ **多模型+流式**：发送按钮旁切换本轮模型，实时展示工具调用和审批过程。

## 📋 环境要求

- Python 3.11+、`uv`、PostgreSQL 14+
- 一个模型 API（默认 DeepSeek，兼容 OpenAI 接口）
- Node.js 20+（`scripts/start.sh` 一键启动需要；只调后端可免）

## 🚀 快速开始

### 1. 获取代码

```bash
git clone <your-repository-url>
cd melonclaw
cp .env.example .env
```

### 2. 填必填配置

数据库需提前创建：

```bash
createdb melonclaw
```

`.env` 最小可用示例：

```dotenv
DEEPAGENTS_PROVIDER=DEEPSEEK_MODEL_FLASH
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=你的 Key
DEEPSEEK_MODEL_FLASH=你的 Flash 模型名

DATABASE_URL=postgresql+asyncpg://用户名:密码@127.0.0.1:5432/melonclaw
```

可选：`TAVILY_API_KEY`（联网搜索用，不配也能启动）。

### 3. 安装并建表

```bash
uv sync --locked
uv run melonclaw-db-init
```

> 服务启动不建表、不迁移。改表结构后清空数据库再执行本命令。

### 4. 启动

```bash
scripts/start.sh      # 前端 + 后端
scripts/restart.sh    # 重启
scripts/shutdown.sh   # 停止
```

- 前端：`http://127.0.0.1:8001`，后端：`http://127.0.0.1:8000`
- 端口占用时脚本拒绝启动，先跑 `scripts/shutdown.sh`
- 验证：`curl http://127.0.0.1:8000/api/status` 返回 `"ready"`
- 发一条消息能正常回复即跑通

## ⚙️ 必要文件

| 文件 | 作用 | 说明 |
| --- | --- | --- |
| `.env` | 全部凭据和地址 | 从 `.env.example` 复制，不提交 Git；只读环境变量 |
| `mcp.json` | MCP 服务定义 | 无需求可为 `{}`；`"${VAR}"` 占位符取值自 `.env`，单个服务失败不影响启动 |
| 工作区目录 | 会话文件和附件 | 默认 `~/.melonclaw/workspaces`，可用 `MELONCLAW_WORKSPACE_DIR` 修改 |

常用可选变量：`MINIMAX_*` / `OPENAI_*`（换模型）、`MELONCLAW_HOST` / `MELONCLAW_PORT` / `MELONCLAW_FRONTEND_*`（改监听地址）、`MELONCLAW_WORKSPACE_DIR`（换工作区）。

## ⚠️ 排障与边界

排障：

- 端口被占用 → `scripts/shutdown.sh` 后再启动
- 启动提示表结构不一致 → 备份后清空数据库，重跑 `uv run melonclaw-db-init`
- 模型下拉为空 → 检查 `.env` 里对应模型名是否填写

边界：

- `.env` 只放本地，不提交。
- 页面用户/租户是开发模拟，不是生产认证。
- `LocalShellBackend` 不是沙箱，仅限本机 `127.0.0.1` 单用户开发使用。
