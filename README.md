<p align="center">
  <img src="frontend/public/assets/brand/melonclaw-readme.webp" alt="MelonClaw Logo" width="360">
</p>

# MelonClaw

MelonClaw 是基于 Deep Agents 的通用 AI 助手，在一个 Web 界面中完成对话、资料研究、文件处理和多步骤任务。

![MelonClaw 对话与生成网页预览](frontend/public/assets/brand/melonclaw-preview.png)

## 功能

- **多步骤任务**：调用工具和子 Agent 完成资料研究和报告撰写，并可查看执行过程。
- **文件与项目**：上传资料、生成并预览文件；同一项目的会话共享工作区。
- **模型与扩展**：自行配置模型；用 Skill 复用流程，用 MCP 连接外部服务。
- **知识库**：导入资料并在对话中检索。权限、导入和检索见[知识库设计](docs/design-docs/knowledge-base.md)。
- **长期记忆**：跨会话保留信息。
- **定时任务**：用对话或表单设置计划，在固定会话查看结果。

## 快速启动

以下命令均在项目根目录执行。需要人填写的是数据库连接、至少 32 个字符的随机 `MELONCLAW_JWT_SECRET`，以及登录后的模型地址和 API Key。令牌有效期留空时为 1000 小时。已有部署升级到 JWT 版本前，先在服务实际读取的环境文件配置该密钥。其余配置有默认值，留空可以启动。

### 环境

- Python 3.11+（低于 4.0）与 [uv](https://docs.astral.sh/uv/)。
- Node.js 20.16+ 与 npm。
- PostgreSQL 14+。知识库需要 pgvector，见 [pgvector 安装与升级指南](ops/PGVECTOR.md)。
- macOS / Linux；Windows 使用 WSL。启动脚本需要 Bash、`curl`、`lsof`。

### 安装

```bash
git clone https://github.com/wangysasd/melonclaw.git
cd melonclaw
uv sync --locked
npm --prefix frontend ci
if [ ! -f .env ]; then cp .env.example .env; fi
```

### 配置文件

1. 按 [PostgreSQL 准备指南](docs/postgresql-setup.md) 创建账号和数据库。
2. 在 `.env` 填写连接地址。用户名或密码含特殊字符时做 URL 百分号编码。不要提交或公开连接串。

```dotenv
DATABASE_URL=postgresql+asyncpg://<用户>:<密码>@<主机>:<端口>/<数据库名>
```

3. 在该库启用 pgvector，然后再初始化。扩展文件、权限和已有库升级见 [pgvector 指南](ops/PGVECTOR.md)。

```bash
bash ops/setup-pgvector.sh --check
bash ops/setup-pgvector.sh --apply
```

本机还没有扩展文件时，把第二条换成 `bash ops/setup-pgvector.sh --apply --install`。

`DEEPSEEK_API_KEY` 可留空。留空时聊天可以启动；搜索、网页读取、语音、朗读，以及 PDF、Office、图片的 OCR 不可用。其余项见 [.env.example](.env.example) 和[配置说明](docs/configuration.md)。

### 初始化

只在空库上执行。这两条命令不会清库，也不会创建可用模型。

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

资源命令只安装缺失的内置 Skill。初始化创建表结构、基础配置和管理员 `admin`，初始密码为 `admin`。已有数据见[更新项目](#更新项目)。服务启动不会建表或升级。

### 启动

```bash
scripts/start.sh      # 启动前后端
scripts/shutdown.sh   # 关闭
scripts/restart.sh    # 重启
```

打开 [http://127.0.0.1:8001](http://127.0.0.1:8001)。后端为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

脚本使用 `dev` 模式并读取 `.env`。分开启动见[手动启动](docs/configuration.md#手动启动)。

### 确认能对话

1. 用 `admin` / `admin` 登录，并修改初始密码。
2. 打开「拓展 → 模型」，填写供应商地址和 API Key，启用供应商，再添加并启用支持工具调用的模型。Anthropic 选择 Messages API，Base URL 填服务根地址或网关前缀。模型列表端点留空时自动追加 `/v1/models`。
3. 新建对话，选择该模型，发送「介绍一下你能做什么」。收到回复即表示主链路可用。

聊天模型不从环境变量读取，必须在页面配置。

## 数据保存位置

- **PostgreSQL**：会话、长期记忆、模型等业务配置。
- **数据目录**：Skill、知识库原件，默认 `~/.melonclaw/data`。
- **工作区**：会话与项目文件，默认 `~/.melonclaw/workspaces`。

备份这三处。改目录见[配置说明](docs/configuration.md)。

## 按需扩展

| 功能 | 配置方式 |
|---|---|
| 模型 | 「拓展 → 模型」。见[模型目录](docs/design-docs/custom-models.md) |
| Skill | 「拓展 → 技能」，或在聊天中请求安装并审批 |
| MCP | 「拓展 → 连接器」。首次建库后列表为空 |
| 环境变量 | 「拓展 → 环境变量」 |
| 定时任务 | 「定时任务」页面，或在聊天中说明。服务需保持运行，使用北京时间；错过不补跑 |
| 搜索、语音、朗读、OCR | 在 `.env` 设置 `DEEPSEEK_API_KEY` 后重启。未设置不影响聊天启动 |
| 知识库 | 页面创建。导入、权限和检索见[知识库设计](docs/design-docs/knowledge-base.md) |
| 开始聊天 | 打开空会话，在居中的输入框直接输入消息。输入 `/` 可选择技能 |
| 聊天附件 | 见[附件处理](docs/design-docs/chat-attachment-processing.md) |
| 调用监控 | 见 [Langfuse 配置](docs/configuration.md#langfuse-追踪) |

## 更新项目

先等活动任务结束，停止服务，备份数据库、数据目录、工作区和 `.env`。升级前按 [pgvector 升级说明](ops/PGVECTOR.md#3-已有部署升级生产) 在目标库启用扩展。`melonclaw-db-update` 会创建尚不存在的知识库表。

```bash
scripts/shutdown.sh
git pull --ff-only
uv sync --locked
npm --prefix frontend ci
uv run melonclaw-db-update
scripts/start.sh
```

不要用 `melonclaw-db-init` 代替升级。服务器部署见 [Linux 部署说明](docs/deployment.md)。

网站底部显示工信部备案号“京ICP备2026082777号-1”，点击可打开工信部备案系统；全屏工作区向下滚动可见。

## 安全边界

- 只支持单实例、单 Web 进程。
- `dev` 模式的身份切换只用于本机开发。
- Shell 不是安全沙箱，不要向不可信用户开放宿主机执行。
- 数据库和备份可能含凭据。

## 常见问题

- **忘记管理员密码**：`uv run melonclaw-admin-password`
- **数据库连接失败**：确认 PostgreSQL 已启动，并检查 `DATABASE_URL`、库是否存在、账号权限。见[准备指南](docs/postgresql-setup.md)。
- **初始化报 vector / pgvector**：先在目标库启用扩展，再执行初始化。见 [pgvector 指南](ops/PGVECTOR.md)。
- **没有可用模型**：在「拓展 → 模型」检查供应商和模型是否启用，以及地址、API Key。
- **端口被占用**：先 `scripts/shutdown.sh`；仍占用则检查 8000 和 8001。

反馈请到 [GitHub Issues](https://github.com/wangysasd/melonclaw/issues)。不要附带密码、API Key 或完整数据库连接地址。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
