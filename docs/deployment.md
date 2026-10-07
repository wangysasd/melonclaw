# Linux 服务器部署

## 目录与归属

| 路径 | 内容 | 升级处理 |
|---|---|---|
| `/opt/melonclaw/` | Git 仓库、虚拟环境、前端 dist、内置模板 | 停服后原地拉代码更新 |
| `/var/lib/melonclaw/data` | 共享／个人 Skill、导入草稿、操作恢复记录 | 保留、备份，不覆盖 |
| `/var/lib/melonclaw/workspaces` | 项目和会话文件，含 `.attachments`、`.artifacts` | 完整保留、备份 |
| `/etc/melonclaw/melonclaw.env` | 凭据、数据库连接、外部路径、运行配置 | 独立维护，服务账号可读，权限 0600 |
| `/var/log/melonclaw` | 后端日志 | 独立保留、轮转 |
| `/run/melonclaw` | 临时运行状态 | systemd 创建，重启可重建 |
| PostgreSQL 独立存储 | Memory、Checkpoint、聊天、配置、文件索引 | 保留、备份、增量更新 |

服务账号为 `melonclaw`；数据目录仅该账号读写。代码由部署账号维护，不应由运行账号修改。Nginx 需能读取 `frontend/dist`。单机先使用 POSIX 本地持久磁盘；容器重建必须保留挂载卷。多副本部署还需要共享存储、并发与锁方案，不能给每个实例一份独立磁盘并共用数据库。

## 首次部署

在 Linux 上创建固定服务账号，安装 Python、uv、Node.js、PostgreSQL 客户端、Nginx；数据库服务需已配置。部署账号在 `/opt` 下执行 `git clone <仓库地址> melonclaw`，代码固定放在 `/opt/melonclaw/`，不使用软链接，以 `uv sync --locked` 安装，`npm --prefix frontend ci` 和 `npm --prefix frontend run build` 构建前端。公开品牌变量可在构建时显式传入 `MELONCLAW_NAME` 和 `brand`；不把数据库或模型凭据传入浏览器。

管理员准备目录和配置（账号需已创建，以下命令在代码根目录执行）：

```bash
sudo install -d -o melonclaw -g melonclaw -m 0700 /var/lib/melonclaw/data /var/lib/melonclaw/workspaces /var/log/melonclaw
sudo install -d -o root -g melonclaw -m 0750 /etc/melonclaw
sudo install -o melonclaw -g melonclaw -m 0600 ops/melonclaw.env.example /etc/melonclaw/melonclaw.env
```

通过服务器编辑器填写外部配置；不要把实际凭据写进仓库或输出到日志。修改 `DATABASE_URL`、实际站点 Origin，并确认 data/workspace 为外部路径。在 `/opt/melonclaw/` 完成依赖安装后，以服务账号安装模板并初始化全新数据库：

```bash
sudo -u melonclaw env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-resources --service-stopped install-builtins --source /opt/melonclaw/.data/skills/shared
sudo -u melonclaw env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-db-init
```

安装 [systemd 服务](../ops/systemd/melonclaw.service)、[Nginx 示例](../ops/nginx/melonclaw.conf) 和 [日志轮转配置](../ops/melonclaw.logrotate)。Nginx 配置中的域名、请求体上限与实际附件限制同步；示例只包含 HTTP，实际远程站点需配置 HTTPS 或在上游终止 TLS，并让 `MELONCLAW_ALLOWED_ORIGINS` 与浏览器访问地址一致。

```bash
sudo install -m 0644 ops/systemd/melonclaw.service /etc/systemd/system/melonclaw.service
sudo install -m 0644 ops/melonclaw.logrotate /etc/logrotate.d/melonclaw
sudo systemctl daemon-reload
sudo systemctl enable --now melonclaw
```

Nginx 站点目录因发行版不同，按该机约定安装示例并运行 `nginx -t` 后 reload。健康检查为 `/api/ready`。systemd 负责进程，不需要自行写 PID；文件日志采用 copytruncate 轮转，轮转瞬间有小量日志损失窗口，不把它当审计存储。

## 已有部署迁移

1. 停止所有服务进程和任务写入；备份数据库、原数据根、原工作区及配置。
2. 以可读取旧目录的账号执行下列命令，保持相对结构和隐藏文件。随后把新目录所有权交给服务账号。

```bash
uv run melonclaw-resources --service-stopped migrate --source <旧数据根> --destination /var/lib/melonclaw/data
uv run melonclaw-resources --service-stopped migrate --source <旧工作区根> --destination /var/lib/melonclaw/workspaces
```

该命令先检查全部冲突，拒绝覆盖不同内容、拒绝符号链接和特殊文件；复制后校验 SHA-256，源目录不删除，中断后可重跑。`--service-stopped` 是操作者的停服声明，不是自动锁住所有写入的机制。失败时保持停服，解决冲突后再执行。

3. 在外部配置切换两个根路径。数据库 `projects.workdir_path`、`skills.storage_path` 保存相对路径，附件目录由 UUID 推导，产物链接相对工作区；正常数据无需改表、改索引或重新 db-init。如果旧记录实际含绝对路径，停止迁移并先制定显式数据修复，不能批量字符串替换聊天内容或 MCP 配置。
4. 启动服务，确认 Skill 文件与数据库索引一致，历史文件下载、附件读取、记忆与会话恢复正常。
5. 核对后清理仓库中的旧运行数据，保留版本化内置模板。备份继续保留在代码目录之外。

## 升级、回滚与备份

升级前记录当前 Git 提交并停止服务，备份一致的数据库、数据根和工作区。在 `/opt/melonclaw/` 执行 `git pull --ff-only`、`uv sync --locked`、`npm --prefix frontend ci` 和 `npm --prefix frontend run build`。若版本包含数据库升级，使用外部配置以服务账号执行 `melonclaw-db-update`，成功后重启服务并检查 `/api/ready`。模板安装命令只填充缺失的 Skill，不更新已有同名 Skill；更新内置内容走资源管理流程。

回滚代码前确认数据库结构与旧版本兼容；切回已记录的旧 Git 提交后，重新同步依赖和构建前端，再启动服务。代码回滚不会自动回滚数据库 schema。异机备份需要包含 PostgreSQL、整个 data/workspaces 和受控保存的配置，定期演练恢复；数据库与磁盘应在同一停写窗口或一致快照中备份。

## 当前验证边界

本机已执行文件迁移、数据库相对路径及 Skill 正文核对；Linux systemd、Nginx 和日志轮转需在目标服务器验收。目录分离不提供多用户 Shell 隔离：当前 LocalShellBackend 不是安全沙箱，共享远程部署仍需解决身份、执行隔离和授权边界后再开放给不可信用户。
