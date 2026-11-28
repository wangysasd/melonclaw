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
- **意见反馈**：登录页和登录后头像菜单都提供统一入口，可提交问题、建议、投诉或举报并查询进度。管理员在系统管理中处理。见[投诉举报](docs/design-docs/reports.md)。

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

侧栏「系统状态」中的模型列表只显示当前可用模型；可用状态不代表已完成连接检测。
| 定时任务 | 「定时任务」页面，或在聊天中说明。服务需保持运行，使用北京时间；错过不补跑。系统管理员可归档或删除任意任务 |
| 搜索、语音、朗读、OCR | 在 `.env` 设置 `DEEPSEEK_API_KEY` 后重启。未设置不影响聊天启动 |
| 知识库 | 页面创建。系统管理员可停用、恢复或删除整库，并查看某个库的操作记录。导入、权限和检索见[知识库设计](docs/design-docs/knowledge-base.md)。已有库执行 `uv run melonclaw-db-update` |
| 开始聊天 | 打开空会话，在居中的输入框直接输入消息。输入 `/` 可选择技能 |
| 聊天附件 | 见[附件处理](docs/design-docs/chat-attachment-processing.md) |
| 调用监控 | 见 [Langfuse 配置](docs/configuration.md#langfuse-追踪) |
| 短信注册、登录与找回密码 | 默认关闭。开启后支持自助注册、短信登录、首次绑号和手机验证码忘记密码重置；启用方式、变量和真实短信联调见[短信注册与登录](docs/design-docs/sms-registration-login.md) |
| 注册核验记录 | 「系统管理 → 用户管理」按用户 ID 或验证编号搜索，点击「手机号已验证」查看注册时间、核验成功时间、方式和手机尾号。查找已删除账号时勾选「含已删除的注册账号」 |

## 更新项目

先等活动任务结束，停止服务，备份数据库、数据目录、工作区和 `.env`。升级前按 [pgvector 升级说明](ops/PGVECTOR.md#3-已有部署升级生产) 在目标库启用扩展。`melonclaw-db-update` 会创建尚不存在的知识库表。

已有库通过同一命令应用 `20261126_registration_verification`、`20261128_sms_reset_password` 等增量升级。升级保留已有账户和手机号绑定，留痕从升级后的自助注册开始。

```bash
scripts/shutdown.sh
git pull --ff-only
uv sync --locked
npm --prefix frontend ci
uv run melonclaw-db-update
scripts/start.sh
```

不要用 `melonclaw-db-init` 代替升级。服务器部署见 [Linux 部署说明](docs/deployment.md)。

正式运营前如需物理删除存量用户和租户、仅保留 `system/admin`，可用 `uv run python ops/purge_legacy_accounts.py` 预览清理范围。正式执行前按 [一次性账户清理](docs/deployment.md#一次性账户清理) 停服并备份。

登录页底部显示工信部备案号“京ICP备2026082777号-1”，点击可打开工信部备案系统；登录后的工作区不展示。

## 投诉举报与反馈

- **访客**可从 MelonClaw 或 RMS 登录页点「意见反馈」。联系方式可以留空。提交后请保存一次性显示的工单编号和私密查询码，之后用两者在同一页面查询。
- **登录用户**从头像菜单打开「意见反馈」，点「提交反馈」。投诉或举报时可在说明里提供文档名、发生时间、账号名称或必要片段。提交后点「查看进度」，也可回到意见反馈列表按工单编号查询。
- **管理员**进入「系统管理 → 投诉举报」，可按编号或状态查找工单，受理、留下内部备注、回复和办结。办结时填写给用户的处理结果，并记录实际采取的措施。如需暂停知识库共享，请通过知识库管理的停用操作处理。

在 Web 实际读取的部署环境文件中填写值守邮箱 `MELONCLAW_REPORT_CONTACT_EMAIL`，可选填 `MELONCLAW_REPORT_CONTACT_NAME` 和承诺响应小时数 `MELONCLAW_REPORT_RESPONSE_HOURS`。只填写实际有人接收的渠道和能够兑现的时限，配置后重启 Web。邮箱留空时可以继续使用站内表单。[配置说明](docs/configuration.md)

已有库在处理活动运行、停止 Web 并备份后执行 `uv run melonclaw-db-update`，然后重启 Web。首次空库执行 `uv run melonclaw-db-init`。生产反代需覆盖来源地址头，以便匿名限流按访问者地址生效。[部署说明](docs/deployment.md#投诉举报)

## 用户协议、隐私政策与个人信息申请

登录和注册页面提供用户协议与隐私政策，分别可通过 /terms 和 /privacy 访问。

未登录时可打开 [http://127.0.0.1:8001/privacy](http://127.0.0.1:8001/privacy)。注册和短信登录在获取验证码前也能进入同一页面。登录后打开头像菜单「意见反馈」，在列表下方点击「个人信息申请」。页面说明手机号用途、阿里云参与的环节，以及平台实际保存的资料。

申请通过邮件人工受理。在部署环境配置这两项后重启 Web：

| 变量 | 作用 |
|---|---|
| `MELONCLAW_OPERATOR_NAME` | 页面上显示的运营主体名称 |
| `MELONCLAW_REPORT_CONTACT_EMAIL` | 已有的公开联系邮箱。投诉页和隐私申请共用，须有人值守 |

两项都留空时，页面仍可访问，但不会生成邮件链接。不要填写示例地址或无人接收的邮箱。

正式开放自助注册前，先填入真实主体和值守邮箱，打开 `/privacy` 核对名称和地址，向该邮箱发一封测试信并确认收到，再回复一封测试信。没有完成这次收件和回复之前，不要把注册开关当作已经提供了个人信息申请渠道。

负责人收到邮件后登记日期和申请类型，按风险核验账号归属，然后处理并回复。可以核对用户 ID 和对方主动提供的手机尾号，不要让对方发送密码或短信验证码。停用只限制账号使用。注销会将账号标为已删除，聊天、文件和注册核验记录仍保留，手机号绑定会释放，可用于注册新账号。删除指定信息只包括本次实际删除的内容。回复应说明已执行的操作，以及仍需保留的安全日志、备份和其他资料。

## 安全边界

- 只支持单实例、单 Web 进程。
- `dev` 模式的身份切换只用于本机开发。
- Shell 不是安全沙箱，不要向不可信用户开放宿主机执行。
- 数据库和备份可能含凭据。
- 登录、发消息和其他写操作追加到数据目录的 `audit/`，至少保留 184 天，覆盖任意连续六个自然月。口令、验证码、完整手机号和令牌不写入该日志。生产环境要更新 Nginx 并重载，日志里的来源才是访问者地址。写入失败不改变业务结果。要让安全负责人收到告警，须配置接收地址并用测试接收端确认送达，见[安全审计日志](docs/design-docs/security-audit-log.md)。正式部署的自评验证和协查流程见[安全自评与监管协查](docs/design-docs/security-assessment-operations.md)。
- 短信验证默认关闭。未配置或云服务失败时，已有账号仍用密码登录。启用 dev 免密时，入口位于密码登录按钮下方。开放自助注册前，先关闭 dev 免密和身份切换。

## 常见问题

- **忘记管理员密码**：`uv run melonclaw-admin-password`
- **数据库连接失败**：确认 PostgreSQL 已启动，并检查 `DATABASE_URL`、库是否存在、账号权限。见[准备指南](docs/postgresql-setup.md)。
- **初始化报 vector / pgvector**：先在目标库启用扩展，再执行初始化。见 [pgvector 指南](ops/PGVECTOR.md)。
- **没有可用模型**：在「拓展 → 模型」检查供应商和模型是否启用，以及地址、API Key。
- **端口被占用**：先 `scripts/shutdown.sh`；仍占用则检查 8000 和 8001。

反馈请到 [GitHub Issues](https://github.com/wangysasd/melonclaw/issues)。不要附带密码、API Key 或完整数据库连接地址。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
