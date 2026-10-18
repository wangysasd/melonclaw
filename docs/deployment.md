# Linux 服务器部署

本次 Ubuntu 从拉代码到 HTTPS 的完整操作、每步作用和验收记录见 [首次上线流程](first-deployment.md)。

## 目录与归属

| 路径 | 内容 | 升级处理 |
|---|---|---|
| `/opt/melonclaw/` | Git 仓库、虚拟环境、前端 dist、内置模板 | 停服后原地拉代码更新 |
| `/var/lib/melonclaw/data` | 共享／个人 Skill、导入草稿、操作恢复记录 | 保留、备份，不覆盖 |
| `/var/lib/melonclaw/workspaces` | 项目和会话文件，含 `.attachments`、`.artifacts` | 完整保留、备份 |
| `/etc/melonclaw/melonclaw.env` | 凭据、数据库连接、外部路径、运行配置 | 独立维护，服务账号可读，权限 0600 |
| `/var/log/melonclaw` | Web（含任务执行）日志 | 独立保留、轮转 |
| `/run/melonclaw` | 临时运行状态 | systemd 创建，重启可重建 |
| PostgreSQL 独立存储 | Memory、Checkpoint、聊天、配置、文件索引、任务与运行结果 | 保留、备份、增量更新 |

统一使用现有 `ubuntu` 账号拉取代码、安装依赖、构建前端和运行服务，不额外创建系统账号。代码、数据、配置和日志由 `ubuntu:ubuntu` 持有；配置文件权限 0600，持久数据目录权限 0700。应用 Shell 与部署账号权限相同，可以修改该账号有权访问的代码和文件；当前方案用于可信用户运行。Nginx 需能读取 `frontend/dist`。单机先使用 POSIX 本地持久磁盘；容器重建必须保留挂载卷。多副本部署还需要共享存储、并发与锁方案，不能给每个实例一份独立磁盘并共用数据库。

## 首次部署

使用服务器现有 `ubuntu` 账号，安装 Python、uv、Node.js、PostgreSQL 客户端、Nginx；数据库服务需已配置。`ubuntu` 在 `/opt` 下执行 `git clone <仓库地址> melonclaw`，代码固定放在 `/opt/melonclaw/`，不使用软链接，以 `uv sync --locked --default-index https://pypi.org/simple` 按仓库锁文件安装，`npm --prefix frontend ci` 和 `npm --prefix frontend run build` 构建前端。显式指定与锁文件一致的官方索引，避免服务器级 uv 镜像设置改写或导致锁文件校验失败。公开品牌变量可在构建时显式传入 `MELONCLAW_NAME` 和 `brand`；不把数据库或模型凭据传入浏览器。

以 `ubuntu` 登录后准备目录和配置（需要管理员权限的命令使用 sudo，以下命令在代码根目录执行；已有配置保留）：

```bash
sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/melonclaw /var/lib/melonclaw/data /var/lib/melonclaw/workspaces /var/log/melonclaw
sudo install -d -o ubuntu -g ubuntu -m 0700 /etc/melonclaw
if ! sudo test -e /etc/melonclaw/melonclaw.env; then
  sudo install -o ubuntu -g ubuntu -m 0600 ops/melonclaw.env.example /etc/melonclaw/melonclaw.env
fi
```

通过服务器编辑器填写外部配置；不要把实际凭据写进仓库或输出到日志。修改 `DATABASE_URL`、实际站点 Origin，并确认 data/workspace 为外部路径。在 `/opt/melonclaw/` 完成依赖安装后，以 `ubuntu` 账号安装模板并初始化全新数据库：

```bash
env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-resources --service-stopped install-builtins --source /opt/melonclaw/.data/skills/shared
env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-db-init
```

安装 [Web systemd 服务](../ops/systemd/melonclaw.service)、[Nginx 示例](../ops/nginx/melonclaw.conf) 和 [日志轮转配置](../ops/melonclaw.logrotate)。Nginx 配置中的域名、请求体上限与实际附件限制同步；示例只包含 HTTP，实际远程站点需配置 HTTPS 或在上游终止 TLS，并让 `MELONCLAW_ALLOWED_ORIGINS` 与浏览器访问地址一致。

```bash
sudo install -m 0644 ops/systemd/melonclaw.service /etc/systemd/system/melonclaw.service
sudo install -m 0644 ops/melonclaw.logrotate /etc/logrotate.d/melonclaw
sudo systemctl daemon-reload
sudo systemctl enable --now melonclaw
```

Nginx 站点目录因发行版不同，按该机约定安装示例并运行 `nginx -t` 后 reload。健康检查为 `/api/ready`。systemd 负责进程，不需要自行写 PID；文件日志采用 copytruncate 轮转，轮转瞬间有小量日志损失窗口，不把它当审计存储。

## 已有部署迁移

1. 停止所有服务进程和任务写入；备份数据库、原数据根、原工作区及配置。
2. 以可读取旧目录的账号执行下列命令，保持相对结构和隐藏文件。新目录所有权应为 `ubuntu:ubuntu`。

```bash
uv run melonclaw-resources --service-stopped migrate --source <旧数据根> --destination /var/lib/melonclaw/data
uv run melonclaw-resources --service-stopped migrate --source <旧工作区根> --destination /var/lib/melonclaw/workspaces
```

该命令先检查全部冲突，拒绝覆盖不同内容、拒绝符号链接和特殊文件；复制后校验 SHA-256，源目录不删除，中断后可重跑。`--service-stopped` 是操作者的停服声明，不是自动锁住所有写入的机制。失败时保持停服，解决冲突后再执行。

3. 在外部配置切换两个根路径。数据库 `projects.workdir_path`、`skills.storage_path` 保存相对路径，附件目录由 UUID 推导，产物链接相对工作区；正常数据无需改表、改索引或重新 db-init。如果旧记录实际含绝对路径，停止迁移并先制定显式数据修复，不能批量字符串替换聊天内容或 MCP 配置。
4. 启动服务，确认 Skill 文件与数据库索引一致，历史文件下载、附件读取、记忆与会话恢复正常。
5. 核对后清理仓库中的旧运行数据，保留版本化内置模板。备份继续保留在代码目录之外。

## 升级、回滚与备份

升级前记录当前 Git 提交并停止 Web 服务，备份一致的数据库、数据根和工作区。在 `/opt/melonclaw/` 执行 `git pull --ff-only`、`uv sync --locked --default-index https://pypi.org/simple`、`npm --prefix frontend ci` 和 `npm --prefix frontend run build`。Nginx 应使用 `ops/nginx/melonclaw.conf` 中对 `/index.html` 设置的 `Cache-Control: no-store`，使浏览器在发版后及时加载新入口与哈希资源；使用自定义配置时应保留该行为。若版本包含数据库升级，标准部署可直接运行 `uv run melonclaw-db-update`：命令会自动读取 `/etc/melonclaw/melonclaw.env`，与 systemd 服务使用相同配置。若配置文件放在其他位置，先设置 `MELONCLAW_ENV_FILE=/实际配置文件路径` 再运行。成功后重启 Web，检查包含任务执行器的 `/api/ready`。模板安装命令只填充缺失的 Skill，不更新已有同名 Skill；更新内置内容走资源管理流程。

回滚代码前确认数据库结构与旧版本兼容；切回已记录的旧 Git 提交后，重新同步依赖和构建前端，再启动服务。代码回滚不会自动回滚数据库 schema。异机备份需要包含 PostgreSQL、整个 data/workspaces 和受控保存的配置，定期演练恢复；数据库与磁盘应在同一停写窗口或一致快照中备份。

## 当前验证边界

本机已执行文件迁移、数据库相对路径及 Skill 正文核对；2026-10-07 远程 Ubuntu 已确认 systemd 后端运行、本机 Nginx API／首页可访问、证书签发部署成功及 HTTPS 登录页可见。证书续期测试、日志轮转和完整业务场景仍需验收，详见 [首次上线记录](first-deployment.md#10-使用验收和当前结论)。目录分离不提供多用户 Shell 隔离：当前 LocalShellBackend 不是安全沙箱，共享远程部署仍需解决身份、执行隔离和授权边界后再开放给不可信用户。

## 已按旧账号准备目录时

先停止应用服务，再将应用数据、日志、配置和已有运行目录的属主改为 `ubuntu:ubuntu`；如果这些目录尚不存在，按首次部署步骤创建。若代码或虚拟环境原先由 root 或其他账号创建，也需将 `/opt/melonclaw` 交给 ubuntu，避免后续 git pull 和依赖安装失败。

```bash
sudo systemctl stop melonclaw
sudo chown -R ubuntu:ubuntu /opt/melonclaw /var/lib/melonclaw /var/log/melonclaw /etc/melonclaw
sudo chmod 0700 /etc/melonclaw
sudo chmod 0600 /etc/melonclaw/melonclaw.env
```

重新安装本仓库 systemd 和 logrotate 模板，运行 `sudo systemctl daemon-reload` 后启动。`/run/melonclaw` 由 systemd 按新 User/Group 重建，不手动创建 PID 文件；Nginx 保持其自身系统账号运行。


## 定时任务上线与运维

任务执行内置于 Web 的 APScheduler，只启动 melonclaw 服务即可。支持单实例 Web；不要增加 Uvicorn/Gunicorn workers 或同时运行新旧应用。数据库会话锁拒绝第二个任务执行器，PostgreSQL 代理必须用会话池模式。后台任务和聊天共享进程资源，Web 停止会中断正在执行的任务。

已有应用首次接入定时任务时，在停写窗口备份数据库、data/workspaces 与配置，停止 Web 后更新依赖和数据库。日常发版不重复初始化：

```bash
sudo systemctl stop melonclaw
```

```bash
cd /opt/melonclaw
uv sync --locked --default-index https://pypi.org/simple
npm --prefix frontend ci
npm --prefix frontend run build
uv run melonclaw-db-update
sudo systemctl start melonclaw
systemctl is-active melonclaw
curl -f http://127.0.0.1:8000/api/ready
```

`20261010_task_center` 一次创建当前完整三表与固定会话约束；重复更新无变化，服务启动不自动建表或升级。未发布的旧任务结构不提供迁移，须在备份、停服后显式重建任务相关表，保留账户、模型和普通聊天；不要用清空记录代替结构重建，db-init 不负责清库。

`/api/ready` 包含 Web 与任务执行器就绪，登录后 `/api/tasks/status` 返回 executor.ready/running/last_scan_at/error。第二个执行器启动失败或扫描连接失效时就绪探测失败；检查 backend.log 并重启单个 Web。默认每 2 秒扫描，全局并发 2、每用户 1；最长排队 24 小时。检查一直排队的任务时同时查看执行器状态、用户并发和模型配置。

正常停服将执行中的任务标 service_shutdown；异常退出留下的 running 在下次启动获取单实例锁后标 service_restarted；保留部分结果、不自动重跑。queued 保持排队并在启动后领取。上线前尽量等运行完成，停机错过的计划直接跳过、不创建记录或补跑。已发出的远端请求不能保证撤销。

常规发版用 [ops/deploy.sh](../ops/deploy.sh)，只停止/启动 Web，失败保持服务停止；它不执行 schema 更新，改表按本节手动步骤执行。日志统一在 `/var/log/melonclaw/backend.log`，包含 task_id/run_id 和状态，不记录凭据、原始异常或完整报告。三张任务表、消息引用元数据及升级账本均需备份；Checkpoint 按保留期定向清理，报告和聊天保留；完整操作回执按保留期清空响应，拒重键不删除。

本地验收使用真实 PostgreSQL、FastAPI/Cookie 和 APScheduler，模型为离线替身，覆盖正常停止、强制退出恢复、单实例竞争和连接失效。未部署线上；Linux systemd 安装与日志轮转仍需服务器实机验证。


已有任务中心在本版本需停 Web 后运行 `uv run melonclaw-db-update`，执行 `20261015_task_reliability`，保留任务、结果、聊天和幂等键，仅删除独立通知字段/索引并调整回执响应与清理索引。Linux 标准部署会自动读取 `/etc/melonclaw/melonclaw.env`；非标准路径需设置 `MELONCLAW_ENV_FILE`。不要重建当前任务表；服务启动不自动升级。
