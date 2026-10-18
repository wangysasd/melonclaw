# 配置与手动启动

首次使用请先按[本地快速启动](../README.md#本地快速启动)安装依赖并初始化数据库。本文补充可选配置和手动启动方式。

## 数据库连接

本地开发使用根目录 `.env`；Linux 标准部署使用 `/etc/melonclaw/melonclaw.env`。如需其他外部文件，可设置 `MELONCLAW_ENV_FILE` 指向该文件。`DATABASE_URL` 必须指向已创建的 PostgreSQL 数据库，应用账号需要连接和建表权限。连接格式见 [.env.example](../.env.example)；用户名或密码中的 URL 特殊字符需百分号编码。

例如，本地 PostgreSQL 认证配置完成后，可创建数据库：

```bash
createdb melonclaw
```

首次建库执行 `uv run melonclaw-db-init`，保留历史数据的升级执行 `uv run melonclaw-db-update`。CLI 与服务按相同规则加载配置：显式 `MELONCLAW_ENV_FILE` 优先，其次标准 Linux 配置文件，再回退到仓库根目录 `.env`。服务启动不会自动建表或升级；不要用反复初始化来排查连接问题。

## 可选环境变量

后端默认读取根目录 `.env`，已导出的同名环境变量优先。完整配置项见 [.env.example](../.env.example)。不要提交 `.env`，也不要在日志、截图或问题反馈中公开实际连接串、Key、Token。

| 变量 | 用途与默认值 |
|---|---|
| `DEEPSEEK_API_KEY` | 内置 DeepSeek 联网搜索与网页读取工具凭据；留空不影响启动，依赖该凭据的搜索与读取能力不可用 |
| `MELONCLAW_DATA_DIR` | Skill 等持久资源目录，默认 `~/.melonclaw/data` |
| `MELONCLAW_WORKSPACE_DIR` | 会话与项目文件目录，默认 `~/.melonclaw/workspaces` |
| `MELONCLAW_TASK_EXECUTION_TIMEOUT_SECONDS` | 后台完整运行时限（包含模型解析、历史摘要、Agent 构建和执行），默认 1800 秒，须为正整数 |
| `MELONCLAW_TASK_RESULT_MAX_CHARS` | 任务文字结果上限，默认 200000 字符，超限明确失败并保留部分内容 |
| `profile` | 仅 `dev` 开启免密登录和全员身份切换；直接启动后端时按此值生效 |

持久数据目录和工作区必须位于项目代码目录之外。仓库里的内置 Skill 是分发模板，安装后使用外部数据目录中的运行时副本。

模型名称、服务地址和 API Key 在「拓展 → 模型」配置。初始化只写入未启用、无凭据的供应商模板，不创建默认模型，也不从环境变量自动生成模型。无需 MCP 时可不创建 `mcp.json`。

### Langfuse 追踪

| 环境变量 | 用途与默认值 |
|---|---|
| `LANGFUSE_TRACING_ENABLED` | 默认 false；true 开启，重启 Web 生效 |
| `LANGFUSE_BASE_URL` | 项目所在区域地址；启用时须明确填写，不默认发送到其他区域 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 项目凭据；仅写环境配置，不提交、不打印、不透传 Agent Shell |
| `LANGFUSE_TRACING_ENVIRONMENT` | 默认 development；生产部署显式设置 production |
| `MELONCLAW_LANGFUSE_CAPTURE_CONTENT` | 默认 true，上传脱敏文本；false 隐藏输入/输出、模型参数与非白名单元数据，保留业务标识和用量 |

地址示例为 `https://jp.cloud.langfuse.com`。从该区域项目设置取得凭据。安装依赖执行 `uv sync --locked`，启动执行 `uv run melonclaw-web`，已有服务重启即可，无 schema 变化。开关开启但凭据缺失或地址无效时记录安全警告并停用监控；布尔变量格式错误仍按应用配置错误处理。

SDK 异步批量上报，不在每条消息结束时同步 flush。网络故障会丢失监控数据，不能把 trace 当业务账本；业务记录和权限继续以本地数据库为准。SDK 媒体上传被强制关闭，普通文本可能含附件派生内容；最终 exporter 同时处理 OTel 错误状态、异常事件和 links。邮箱、常见大陆手机号、常见凭据与带密码 URL 自动脱敏，但不构成完整的 PII/DLP 检测。需要严格不外发正文时关闭内容采集。详见[监控设计](design-docs/langfuse-tracing.md)。

### DM Skill 的 Shell 配置

若已在外部数据根安装 DM 脚本，通过“拓展 → 环境变量”配置其需要的 `DM_APP_KEY`、`DM_APP_SECRET` 和 `DM_FIXED_INCOME_API_SKILL_DIR`。管理员保存为系统共享，普通用户保存为个人配置。路径需填写实际字面路径，不展开 `${HOME}`；不再通过 `.env` 默认名单或 `MELONCLAW_SHELL_ENV_ALLOWLIST` 透传业务变量。共享变量可被获得它的脚本使用，不提供宿主机安全隔离。

新版本先停服备份，执行 `uv run melonclaw-db-update`；启动受控管理阶段在“拓展 → 环境变量”录入必需共享变量，核对后从 `.env` 删除对应业务配置，再开放聊天。新轮生效，旧轮保留其环境。该配置不安装独立的客户端或 Skill；脚本和依赖仍需按资源管理流程安装。更多限制见[环境变量与准备缓存](design-docs/environment-and-preparation.md)。

## 手动启动

完成首次初始化后，可以在项目根目录的两个终端分别启动前后端，方便直接查看输出。

终端一，启动后端：

```bash
uv run melonclaw-web
```

终端二，启动前端：

```bash
npm --prefix frontend run dev
```

前端默认地址为 [http://127.0.0.1:8001](http://127.0.0.1:8001)，后端为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。停止时在对应终端按 Ctrl+C。

与手动启动不同，`scripts/start.sh` 默认显式使用 `dev`，读取 `.env` 并开启开发身份切换，仅用于本机开发。前端代理和构建配置见[前端开发文档](FRONTEND.md)，服务器目录、升级和备份见[部署文档](deployment.md)。

### Web 内置任务调度

以上执行限制与以下参数均须为正整数。APScheduler 的周期扫描任务只保存在内存，业务任务与计划以数据库为准。

| 环境变量 | 含义 |
|---|---|
| `MELONCLAW_TASK_POLL_SECONDS` | 扫描计划/队列、进度变化保存与取消检查间隔（秒），默认 2；正文未变化不保存 |
| `MELONCLAW_TASK_GLOBAL_CONCURRENCY` | 单执行器全局并发上限，默认 2 |
| `MELONCLAW_TASK_USER_CONCURRENCY` | 每用户并发上限，默认 1 |
| `MELONCLAW_TASK_QUEUE_TIMEOUT_SECONDS` | 排队超时（秒），默认 86400 |
| `MELONCLAW_TASK_CHECKPOINT_RETENTION_DAYS` | 终态任务 Checkpoint 保留天数，默认 30；保留业务结果与聊天 |
| `MELONCLAW_TASK_OPERATION_RETENTION_DAYS` | 完整操作回执保留天数，默认 30；过期拒绝重放，清理仅移除响应，永久保留请求键/摘要以防重复执行 |
| `MELONCLAW_TASK_HISTORY_TIMEOUT_SECONDS` | 后台/追问的历史准备时限（秒），默认 60；追问包含来源读取，超时释放会话锁 |
| `MELONCLAW_TASK_HISTORY_SUMMARY_MAX_CALLS` | 每轮历史摘要最多调用次数，默认 4，同时最多为模型调用预算减 1；超限提前失败，不截断历史 |
| `MELONCLAW_TASK_CLEANUP_INTERVAL_SECONDS` | Checkpoint 与过期操作回执分批清理检查间隔（秒），默认 3600；每类每轮最多 100 条 |

任务调度随 Web 自动启动，不需要额外服务。只支持单实例 Web，第二个执行器无法获得数据库会话锁时拒绝启动；连接池代理必须使用会话模式，不能用事务池模式。`/api/ready` 同时探测 Web 与执行器，登录后的 `/api/tasks/status` 提供 executor 状态。详见[定时任务运维](deployment.md#定时任务上线与运维)。
