# MelonClaw

MelonClaw 是基于 Deep Agents 的通用 AI 助手，在浏览器中完成研究、写作、文件处理和多步骤任务。你可以上传资料、提出任务，在同一个界面查看执行过程、确认敏感操作，并预览或下载生成的文件。

## 核心能力

- **研究与写作**：结合资料和工具完成多步骤任务，通过 Skill 复用任务方法，通过 MCP 连接外部服务。
- **文件处理与交付**：上传附件，在聊天中生成文件，直接预览 HTML、Markdown 等内容并下载结果。
- **项目与长期记忆**：同一项目下的多个会话共享工作区文件，长期记忆帮助跨会话保留信息。
- **可查看、可确认的执行过程**：查看工具调用与任务进展，在需要审批的操作前确认或拒绝。

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

### 2. 配置数据库

编辑根目录 `.env`，**首次启动唯一必填项是 `DATABASE_URL`**：

```dotenv
DATABASE_URL=postgresql+asyncpg://<用户>:<密码>@<主机>:<端口>/<数据库名>
```

替换为自己的 PostgreSQL 连接信息，数据库须提前创建。其他变量可保持默认；连接串格式、持久目录和可选配置见[配置与手动启动](docs/configuration.md)。不要提交 `.env` 或公开实际凭据。

模型在启动后的页面配置；首次启动无需配置 Tavily 或 MCP。

### 3. 首次初始化

```bash
uv run melonclaw-resources --service-stopped install-builtins --source .data/skills/shared
uv run melonclaw-db-init
```

以上命令安装内置 Skill 并初始化数据库，创建管理员 `admin`（初始密码 `admin`）。不会自动清库或覆盖已有 Skill；模型需在页面配置。

### 4. 启动前后端

一键启动：

```bash
scripts/start.sh
```

访问前端 [http://127.0.0.1:8001](http://127.0.0.1:8001)，后端默认地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。一键脚本默认使用 `dev`，读取 `.env` 并开启开发身份切换，仅用于本机开发。

### 5. 配置模型并开始聊天

1. 用 `admin` / `admin` 登录后修改密码。
2. 在「拓展 → 模型」配置供应商地址和 Key，启用供应商，再添加并启用支持工具调用的模型。
3. 新建对话、选择模型并发送消息，收到回复即完成基本验证。

未配置模型时可以管理配置，但不能聊天。之后可以尝试上传一份资料，请助手总结内容并生成 Markdown 文件。

## 停止与更新

```bash
scripts/shutdown.sh           # 停止一键脚本启动的前后端
scripts/restart.sh            # 重启前后端
```

更新代码后重新运行 `uv sync --locked` 和 `npm --prefix frontend ci`。已有数据库需要升级时，停服后执行 `uv run melonclaw-db-update`，再重启；该命令保留业务数据，首次建库才用 `melonclaw-db-init`。服务启动不会自动建表或升级。

## 使用边界

当前适合本机开发与个人使用。`LocalShellBackend` 不是安全沙箱，人工审批不能替代系统隔离；不要向不可信用户开放宿主机执行能力。

## 更多文档

- [配置与手动启动](docs/configuration.md)：环境变量、数据目录与分别启动前后端。
- [开发文档导航](docs/README.md)：架构、前端、设计文档与质量检查。

参与开发时运行 `scripts/check.sh` 验证改动，覆盖后端与前端检查及文档链接校验；未安装前端依赖时跳过前端部分。
