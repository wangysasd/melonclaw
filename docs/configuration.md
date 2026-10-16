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
| `TAVILY_API_KEY` | 内置联网搜索凭据；留空不影响启动，依赖该凭据的搜索能力不可用 |
| `MELONCLAW_DATA_DIR` | Skill 等持久资源目录，默认 `~/.melonclaw/data` |
| `MELONCLAW_WORKSPACE_DIR` | 会话与项目文件目录，默认 `~/.melonclaw/workspaces` |
| `MELONCLAW_TASK_EXECUTION_TIMEOUT_SECONDS` | 后台完整运行时限（包含模型解析、历史摘要、Agent 构建和执行），默认 1800 秒，须为正整数 |
| `MELONCLAW_TASK_RESULT_MAX_CHARS` | 任务文字结果上限，默认 200000 字符，超限明确失败并保留部分内容 |
| `profile` | 仅 `dev` 开启免密登录和全员身份切换；直接启动后端时按此值生效 |

持久数据目录和工作区必须位于项目代码目录之外。仓库里的内置 Skill 是分发模板，安装后使用外部数据目录中的运行时副本。

模型名称、服务地址和 API Key 在「拓展 → 模型」配置。初始化只写入未启用、无凭据的供应商模板，不创建默认模型，也不从环境变量自动生成模型。无需 MCP 时可不创建 `mcp.json`。

### DM Skill 的 Shell 配置

已安装的 DM 脚本需要读取 `DM_APP_KEY`、`DM_APP_SECRET`，依赖共享客户端的脚本还需要 `DM_FIXED_INCOME_API_SKILL_DIR`。仅在 `.env` 填写变量不会自动透传到 Agent Shell；通过现有 `MELONCLAW_SHELL_ENV_ALLOWLIST` 按精确变量名放行：

```dotenv
MELONCLAW_SHELL_ENV_ALLOWLIST=DM_APP_KEY,DM_APP_SECRET,DM_FIXED_INCOME_API_SKILL_DIR
DM_FIXED_INCOME_API_SKILL_DIR=${HOME}/.melonclaw/data/skills/shared/daily-bond-report
```

上例复用已安装 `daily-bond-report/scripts/dm_quant_client.py`；自定义数据根需调整路径。`.env` 的用户目录插值使用 `${HOME}`，裸 `$HOME` 不会展开；不要在代码或示例写入个人机器的绝对路径。凭据仍只在本地环境配置中填写，不放到 Shell 命令参数、输出或额外 JSON 文件。更新后重启 Web，新的 Agent Shell 才会获得配置。

该白名单授权的是部署者共享凭据，所有普通聊天 Agent Shell 均可使用，不提供个人凭据隔离；数据库、应用模型和内置搜索凭据仍受既有黑名单保护。此配置只解决客户端导入和凭据注入，不安装独立的 `dm-fixed-income-api`（`query_api.py`）或 `credit-sensitivity-recovery`；需要这些独立步骤时仍须安装相应 Skill。

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
