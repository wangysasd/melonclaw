# MelonClaw

MelonClaw 是一个基于 Deep Agents 的通用 AI 助手，提供命令行和浏览器两种入口。它可以回答问题、写作改写、整理信息、制定计划，也可以按需联网搜索、调用文件工具、委派子 Agent、执行受限的 JavaScript 计算、接入 MCP 工具，并在写文件或执行 Shell 命令前请求人工审批。

项目默认面向本机或受信任环境运行。Web 入口当前没有登录鉴权，`LocalShellBackend` 也不是安全沙箱；不要直接把服务暴露给公网或不受信任的用户。

## 功能概览

- 通用问答与任务处理：根据用户意图直接回答、解释、写作改写、总结、翻译、分析和制定计划。
- 按需联网搜索：用户明确要求搜索或核验、问题依赖时效性信息，或搜索能显著提高准确性时，通过 Tavily 获取外部资料。
- Agent 文件工作区：使用 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 等文件工具处理用户授权的工作材料。
- 子 Agent：通过 `task` 将相对独立的工作委派给 `general-purpose` 子 Agent。
- Project 与会话：Web 中可以创建 Project；同一 Project 下的多个会话共享文件工作目录，但各自拥有独立的对话状态。
- Global/Tenant/User Memory：全局、租户和个人长期记忆使用 PostgreSQL Store 持久化；读取按当前用户和租户隔离，个人记忆可显式记住/删除，租户记忆默认通过提案发布。
- PostgreSQL 持久化：业务数据、聊天消息、LangGraph Checkpointer、审批中断状态和长期 Memory Store 都保存在 PostgreSQL 中。
- QuickJS Interpreter：通过 `eval` 在无文件、无网络、无 Shell 的 JavaScript 环境中完成循环、聚合和数据变换；当前只允许通过 PTC 调用只读搜索工具。
- Human-in-the-loop：`write_file`、`edit_file`、`delete` 和 `execute` 执行前会暂停，用户可以批准、编辑参数或拒绝。
- Skill：`skills/` 下的 `SKILL.md` 会按需加载；当前包含 `research-workflow` 研究流程。
- MCP：可以从根目录 `mcp.json` 接入远程 HTTP/SSE 或本地 STDIO 服务，包括 Tushare 等外部工具。

## 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 14+（需要先创建数据库）
- 一个模型 API：默认使用 DeepSeek，也支持 OpenAI 兼容接口
- Tavily API Key（只有实际使用联网搜索时必需）

## 快速启动

### 1. 获取代码并创建配置

```bash
git clone <your-repository-url>
cd melonclaw
cp .env.example .env
```

编辑 `.env`。最小的 DeepSeek 配置如下，API Key 不要提交到 Git：

```dotenv
DEEPAGENTS_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=你的 DeepSeek 模型名

TAVILY_API_KEY=你的 Tavily API Key
DATABASE_URL=postgresql+asyncpg://用户名:密码@127.0.0.1:5432/melonclaw
```

`DATABASE_URL` 必须使用 `postgresql+asyncpg://` 驱动。数据库需要提前创建；初始化命令只会创建或升级应用表，不会创建 PostgreSQL 数据库本身。

### 2. 安装依赖并初始化数据库

```bash
uv sync --locked
uv run melonclaw-db-init
```

`melonclaw-db-init` 会幂等创建业务表、演示用户/租户数据、LangGraph PostgreSQL Checkpointer 表和 Memory Store 表；数据库结构升级也通过该命令完成。首次部署或升级后执行一次即可。

### 3. 启动 CLI

```bash
uv run melonclaw
```

启动后在 `🧑>` 提示符输入自然语言问题：

```text
🧑> 请研究 LangGraph 的 StateGraph 和节点模型，并整理成一份中文报告。
```

输入 `exit`、`quit`、`q` 或 `:q` 退出。CLI 使用固定的 `deepagents-quickstart` thread，因此 PostgreSQL 初始化完成后，重新启动仍可以恢复会话状态和未完成的审批。
CLI 当前使用开发种子身份 `zhangsan/research`，用于验证长期 Memory；生产接入时应替换为认证层提供的用户和租户上下文。

### 4. 启动 Web

```bash
uv run melonclaw-web
```

然后打开 <http://127.0.0.1:8000>。也可以使用等价入口：

```bash
uv run python -m melonclaw.main_web
```

修改监听地址或端口：

```bash
MELONCLAW_HOST=0.0.0.0 MELONCLAW_PORT=8080 uv run melonclaw-web
```

接口文档位于 <http://127.0.0.1:8000/docs>。

## Web 使用方式

1. 等待首屏状态显示 Agent 和数据库已就绪。
2. 在模拟用户下拉框中选择用户；首次加载默认选中张三。如果用户属于多个租户，后端会将租户名称以“、”连接展示，当前请求使用后端返回的默认租户上下文。
3. 选择已有 Project，或点击“新增项目”。
4. 在 Project 中新建会话并输入问题。
5. 查看文本、工具调用、子 Agent 和工具结果的实时事件。
6. 如果 Agent 要写文件、删除文件或执行 Shell 命令，在审批卡片中选择批准、编辑 JSON 参数或拒绝。

聊天页面采用整屏布局：左侧会话列表在自己的区域内滚动且保持固定行高，右侧只有聊天消息区滚动，底部输入框固定在页面下方。

对话运行会按当前租户加载 Global/Tenant/User Memory。只有用户明确要求长期保存时才写入个人 Memory；普通成员提出的租户共享内容会形成提案，不会直接修改已发布的租户 Memory。

`tenant_id` 是请求的当前运行上下文，用于校验用户租户成员关系和选择 Tenant Memory；Conversation 本身只按 `user_id + project_id` 归属。因此，用户切换租户标签后，仍可看到和继续使用自己已有的会话，但本轮会读取当前租户的 Tenant Memory。

同一 Project 下的多个会话共享 Project 工作目录；会话的消息、Checkpointer 状态、HITL 中断和 Interpreter thread 状态仍然彼此隔离。Project 工作目录默认位于 `~/.melonclaw/workspaces`，可以通过 `MELONCLAW_WORKSPACE_DIR` 指定其他根目录。

页面中的用户和租户是开发用模拟身份，不是登录系统。后端仍会重新校验用户、租户标签、Project 和 Conversation 的归属，但不能代替生产环境的认证和授权。

## 常用功能

### 研究与联网搜索

配置 `TAVILY_API_KEY` 后，可以直接提出需要实时资料的问题：

```text
请比较 LangGraph 当前的持久化方案，并给出官方文档依据。
```

Agent 会按需调用 `internet_search`。如果不配置 Tavily，应用仍可以启动，但联网搜索调用会失败。

### 文件工作区

CLI 的研究中间文件写入进程临时目录，默认位于仓库根目录的 `temp/melonclaw-*`，进程退出时清理。Web 的文件操作写入 Project 的持久工作目录，不会写入仓库源码。

在 Agent 看到的虚拟工作区中，文件应使用类似 `/summary.md` 的路径，而不是把宿主机的绝对路径传给文件工具。

### Interpreter

可以要求 Agent 使用 `eval` 做纯计算或数据处理：

```text
请务必使用 eval 计算 1 到 100 的平方和，不要使用 execute。
```

预期结果为 `338350`。Interpreter 没有文件、网络、Shell、包管理器和真实时钟，适合处理循环、分组、排序、聚合等 Agent 内部控制流，不是执行项目命令的 Sandbox。

同一 CLI thread 或 Web conversation 中，可序列化的 JavaScript 状态会随 PostgreSQL Checkpoint 恢复；新建 Web 会话不会读取旧会话的 Interpreter 状态。

### 人工审批

以下操作默认需要审批：

- `write_file`
- `edit_file`
- `delete`
- `execute`

审批选项：

- `approve` / `a`：按原参数执行
- `edit` / `e`：修改完整 JSON 参数后执行，工具名称不能替换
- `reject` / `r`：不执行，并把拒绝原因反馈给 Agent

读取文件、Tavily 搜索和当前只读 MCP 查询不需要审批。

一个 Checkpoint 可能同时包含多个并行 interrupt。审批数据会返回每个 interrupt 的 `id`；单个 interrupt 仍可使用 `actions`，多个 interrupt 则位于 `interrupts` 数组中。提交多个 interrupt 时，`decisions` 使用如下分组形状：

```json
[
  {"interrupt_id": "...", "decisions": [{"type": "approve"}]}
]
```

## MCP 配置

MCP 服务定义放在根目录 `mcp.json`，服务地址和连接类型不放在 `.env`。`.env` 只存放凭据和环境变量占位符。当前仓库的 `mcp.json` 默认处于整段注释状态，因此不启用 MCP；不需要 MCP 时可以保持现状。

启用 Tushare 时，将 `mcp.json` 改为合法 JSON，例如：

```json
{
  "mcpServers": {
    "tushare_mcp": {
      "type": "http",
      "url": "https://api.tushare.pro/mcp/?token=${TUSHARE_MCP_TOKEN}"
    }
  }
}
```

再在 `.env` 中配置：

```dotenv
TUSHARE_MCP_TOKEN=你的 Tushare Token
```

有 token 且没有显式设置服务名时，应用会自动选择 `tushare_mcp`。也可以显式选择或关闭：

```dotenv
DEEPAGENTS_MCP_SERVER_NAMES=tushare_mcp
# DEEPAGENTS_MCP_SERVER_NAMES=
```

Tushare 工具默认全部注册。需要在动态选择前缩小目录时，可以设置：

```dotenv
DEEPAGENTS_TUSHARE_MCP_TOOLS=stock_basic,trade_cal,daily
```

应用会在启动日志中显示 MCP 服务名和工具数量，但不会显示 URL 或 token。DeepSeek 使用项目内的目录选择器；OpenAI provider 使用 LangChain 的 `LLMToolSelectorMiddleware`。两者都会把单次主模型调用的候选工具数量限制在最多 16 个。

### MCP 示例

`example/mcp/` 包含数学 STDIO 和 SSE Server，以及一个通用 Client。使用这些示例前，需要在 `mcp.json` 中配置对应服务；STDIO 命令和相对路径应从仓库根目录执行。

启动 SSE Server：

```bash
uv run python example/mcp/server/math_sse_server.py --port 8000
```

另开终端发现工具：

```bash
uv run python example/mcp/client/mcp_client.py \
  --server math_sse \
  --list-tools
```

## 模型切换

默认 provider 是 DeepSeek。切换 OpenAI 或其他 OpenAI 兼容服务时，填写：

```dotenv
DEEPAGENTS_PROVIDER=openai
OPENAI_API_KEY=你的 OpenAI API Key
OPENAI_MODEL=模型名
OPENAI_BASE_URL=https://api.openai.com/v1
```

然后运行：

```bash
uv run melonclaw
```

## 主要 API

Web 服务启动后，常用接口如下：

```text
GET  /api/status
GET  /api/dev/users

POST /api/projects
GET  /api/projects?user_id=...&tenant_id=...

POST /api/conversations
GET  /api/conversations?user_id=...&tenant_id=...&project_id=...
GET  /api/conversations/{id}/messages?user_id=...&tenant_id=...
POST /api/conversations/{id}/messages
POST /api/conversations/{id}/approval
```

`GET /api/dev/users` 由后端查询 `users`、`user_tenants` 和 `tenants` 后按用户聚合返回；一个用户对应一个下拉选项。用户所属的多个租户名称会由后端拼接为 `租户A、租户B`，前端不维护用户或租户名单。

消息请求示例：

```json
{
  "user_id": "zhangsan",
  "tenant_id": "research",
  "request_id": "6f7d9d8d-6c6f-4e1e-bd2d-4a4a1f5f4f24",
  "content": "请研究 LangGraph 的 StateGraph。"
}
```

`POST /api/conversations/{id}/messages` 和审批接口返回 SSE 流，事件中包含文本、工具调用、工具结果、子 Agent 状态、审批暂停和最终完成状态。

## 配置项

| 变量 | 作用 | 默认值或要求 |
| --- | --- | --- |
| `DEEPAGENTS_PROVIDER` | 模型 provider | `deepseek` |
| `DEEPSEEK_BASE_URL` | DeepSeek/OpenAI 兼容接口地址 | 建议填写 `https://api.deepseek.com` |
| `DEEPSEEK_API_KEY` | DeepSeek 凭据 | 必填（默认 provider） |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | 必填（默认 provider） |
| `OPENAI_API_KEY` | OpenAI 凭据 | 使用 OpenAI 时必填 |
| `OPENAI_MODEL` | OpenAI 模型名 | 使用 OpenAI 时必填 |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址 | 可选 |
| `TAVILY_API_KEY` | 联网搜索凭据 | 使用搜索时必填 |
| `TUSHARE_MCP_TOKEN` | Tushare MCP 凭据 | 启用 Tushare MCP 时必填 |
| `DEEPAGENTS_MCP_SERVER_NAMES` | 显式选择 MCP 服务 | 可选，逗号分隔 |
| `DEEPAGENTS_TUSHARE_MCP_TOOLS` | 限制 Tushare 工具候选目录 | 可选，逗号分隔 |
| `DATABASE_URL` | PostgreSQL 业务和 Checkpointer 连接 | 必填，使用 `postgresql+asyncpg://` |
| `MELONCLAW_WORKSPACE_DIR` | Project 持久工作区根目录 | `~/.melonclaw/workspaces` |
| `MELONCLAW_HOST` | Web 监听地址 | `127.0.0.1` |
| `MELONCLAW_PORT` | Web 监听端口 | `8000` |

## 项目结构

```text
src/melonclaw/
├── core/          配置、Agent、数据库、MCP、提示词和运行时能力
├── middleware/    文件调用排序和动态工具选择
├── output/        事件适配、内容处理和流式输出
├── tool/          Tavily 搜索和工具装配
└── web/           FastAPI、SSE 服务和前端静态文件
example/mcp/       MCP Server/Client 示例
skills/            Agent 可按需读取的 Skill
deploy/            服务器部署脚本、systemd 单元和 Nginx 反向代理配置
mcp.json           MCP 服务目录
note.md            功能演进中的设计决策与验证记录
```

新增能力时，应用代码放在 `src/melonclaw/`，依赖写入 `pyproject.toml` 并更新 `uv.lock`；功能的背景、方案、取舍、验证结果和已知边界同步记录到 [note.md](note.md)。

## 服务器部署

`deploy/` 下的脚本面向 Ubuntu 24.04 + systemd + Nginx，应用以 `melonclaw` 系统用户运行，uvicorn 只监听 `127.0.0.1:8000`，由 Nginx 对外提供 80 端口。

### 1. 准备实例和数据库

- 轻量应用服务器：Ubuntu 24.04，2 核 4 GB 起，与数据库同地域。
- PostgreSQL：可使用腾讯云 TencentDB PostgreSQL（建议开启内网地址并与轻量服务器同 VPC/同地域；跨产品内网不通时改用数据库公网地址加 SSL），也可以选择服务器本机安装。
- 先在数据库中创建空库，例如 `melonclaw`。初始化命令只建表，不建库。
- 防火墙放行 TCP 80；8000 不要对外放行。

### 2. 放置代码并配置环境变量

把项目放到 `/opt/melonclaw`，然后生成并填写 `.env`：

```bash
cp /opt/melonclaw/.env.example /opt/melonclaw/.env
chmod 600 /opt/melonclaw/.env
```

`DATABASE_URL` 必须是 `postgresql+asyncpg://用户名:密码@主机:端口/数据库名`。密钥只在服务器上维护，不要提交到 Git，也不要写进脚本或日志。

### 3. 执行安装脚本

```bash
cd /opt/melonclaw
# 可选：设置 Basic 认证，避免带 Shell 能力的 Agent 直接暴露在公网
export BASIC_AUTH_USER=melonclaw
export BASIC_AUTH_PASSWORD='替换为强密码'
bash deploy/install.sh
```

脚本会依次完成：安装系统依赖和 uv、创建 `melonclaw` 用户、`uv sync --locked`、安装 systemd 单元、执行 `melonclaw-db-init`、配置并重载 Nginx。

`.env` 未填写 `DATABASE_URL` 时，脚本会跳过数据库初始化并提示；补齐后单独执行 `bash deploy/db-init.sh` 再重启服务即可。

### 4. 运维命令

```bash
systemctl status melonclaw-web
journalctl -u melonclaw-web -f
systemctl restart melonclaw-web
```

更新代码后执行 `bash deploy/install.sh`（设置 `GIT_REPO` 可直接拉取），结构变更时先执行 `bash deploy/db-init.sh`。

### 5. 验证

- 打开 `http://<服务器公网IP>/`，首屏状态应显示 Agent 和数据库已就绪。
- 发起一次对话，确认 SSE 事件逐条流式返回而不是一次性输出；Nginx 已关闭 `proxy_buffering`，若被其他网关再次缓冲会导致流式失效。
- `journalctl -u melonclaw-web` 中不应出现 `DATABASE_URL` 或 API Key 明文。

### 安全边界

应用具备文件写入和 Shell 执行工具，`LocalShellBackend` 不是安全沙箱。公网部署时至少做到：只暴露 80/443、开启 Basic 认证或上游认证、`.env` 权限 600、Agent 工作区与仓库源码分离（默认在 `~/.melonclaw/workspaces`）。

## Web 界面与品牌资源

Web 入口继续使用原生 FastAPI HTML/CSS/JavaScript，不引入 React、Tailwind 或运行时 CDN。当前界面保留桌面两栏布局：左侧项目/会话导航，右侧聊天与底部输入区；宽度不超过 768px 时侧栏改为带遮罩的菜单抽屉。

- 欢迎页在用户、项目加载完成后由运行时重新渲染，不会被清空；有可用项目但没有会话时可以先输入，首次发送会在当前/默认 Project 下创建会话并发送草稿。
- 助手回复支持标题、列表、表格、链接、代码块和复制；Markdown 使用本地轻量解析器生成受控 HTML，原始内容保留在 DOM 数据字段中，禁止远程图片，链接仅允许 `http`、`https`、`mailto` 和站内路径。
- 工具过程保留可折叠的调用详情、`call_key`、子 Agent 父子关系和历史事件恢复；摘要显示“搜索资料”“读取文件”“执行计算”等易懂名称，原始工具名仍保留。
- 审批仍沿用 `approve`、`edit`、`reject`、`respond` 与 `allowed_decisions` 约束；参数 JSON 错误显示在对应表单项旁，不改变服务端工具名。
- 状态胶囊由服务状态和 SSE 事件驱动，显示“连接中 / 已就绪 / 处理中 / 等待确认 / 失败”，不虚构 MCP 或联网连接状态。
- 品牌原图和本地 Lucide 资源位于 `src/melonclaw/web/static/assets/`：`brand/melonclaw-mark-white.png` 是保留的白底 RGB 源图，`brand/melonclaw-mark.png` 与 `brand/favicon-48.png` 是首屏/浏览器适配尺寸；`icons/` 同时保留许可证和来源 JSON。

启动 Web：

```bash
uv run melonclaw-web
```

访问 `http://127.0.0.1:8000/`。UI 仍使用 `/api/status`、`/api/dev/users`、`/api/projects`、`/api/conversations` 及既有 SSE/审批接口；模拟用户只是开发入口，不等同于生产认证。若服务端或数据库不可用，页面会显示失败状态，不能据此判断 UI 已完成端到端验收。

## 常见问题

### `DATABASE_URL` 相关错误

确认 PostgreSQL 已启动、目标数据库已创建，并且 URL 使用：

```text
postgresql+asyncpg://用户名:密码@主机:端口/数据库名
```

然后重新执行：

```bash
uv run melonclaw-db-init
```

### Web 显示数据库尚未初始化

先停止 Web 进程，执行 `uv run melonclaw-db-init`，再重新启动 `uv run melonclaw-web`。

### Tavily 搜索失败

确认 `TAVILY_API_KEY` 已配置且账户可用。没有 Tavily Key 时，计算、文件处理和部分不需要联网的请求仍可能正常运行，但搜索工具不可用。

### MCP 工具没有出现

检查 `mcp.json` 是否为合法 JSON、服务名是否与 `DEEPAGENTS_MCP_SERVER_NAMES` 一致，并确认凭据占位符对应的环境变量已配置。整段 `//` 注释的 `mcp.json` 会被应用视为禁用配置。

### 运行时目录和 Project 文件在哪里

CLI 临时文件在仓库根目录 `temp/` 下，随进程退出清理；Web Project 文件在 `MELONCLAW_WORKSPACE_DIR` 指定的持久目录下。两者都不应当写入 Git 仓库。

## 相关文档

- [note.md](note.md)：功能演进过程中的架构分析、设计取舍、验证方式和当前边界。
- [skills/research-workflow/SKILL.md](skills/research-workflow/SKILL.md)：研究任务的执行约束。
- [pyproject.toml](pyproject.toml)：项目依赖和命令入口。
- [mcp.json](mcp.json)：MCP 服务目录。
