# Linux 服务器部署

生产服务器已绑定域名 `melonclaw.cn` 和 `www.melonclaw.cn`。两个主机名的 HTTP 都 301 到同一主机名的 HTTPS，443 端口提供页面和 `/api/`。Ubuntu 从拉代码到 HTTPS 的完整操作、每步作用和验收记录见 [首次上线流程](first-deployment.md)。

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

使用服务器现有 `ubuntu` 账号，安装 Python、uv、Node.js、PostgreSQL 客户端、Nginx；数据库服务需已配置。`ubuntu` 在 `/opt` 下执行 `git clone <仓库地址> melonclaw`，代码固定放在 `/opt/melonclaw/`，不使用软链接，以 `uv sync --locked --default-index https://pypi.org/simple` 按仓库锁文件安装，`npm --prefix frontend ci` 和 `npm --prefix frontend run build` 构建前端。显式指定与锁文件一致的官方索引，避免服务器级 uv 镜像设置改写或导致锁文件校验失败。公开品牌变量 `brand` 可在构建时显式传入；助手名称 `MELONCLAW_NAME` 由后端运行时下发；不把数据库或模型凭据传入浏览器。

以 `ubuntu` 登录后准备目录和配置（需要管理员权限的命令使用 sudo，以下命令在代码根目录执行；已有配置保留）：

```bash
sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/melonclaw /var/lib/melonclaw/data /var/lib/melonclaw/workspaces /var/log/melonclaw
sudo install -d -o ubuntu -g ubuntu -m 0700 /etc/melonclaw
if ! sudo test -e /etc/melonclaw/melonclaw.env; then
  sudo install -o ubuntu -g ubuntu -m 0600 ops/melonclaw.env.example /etc/melonclaw/melonclaw.env
fi
```

通过服务器编辑器填写外部配置；不要把实际凭据写进仓库或输出到日志。填写 `DATABASE_URL`、至少 32 个字符的随机 `MELONCLAW_JWT_SECRET` 和实际站点 Origin，并确认 data/workspace 为外部路径。正式开放自助注册前，再填写真实的 `MELONCLAW_OPERATOR_NAME` 和有人值守的 `MELONCLAW_REPORT_CONTACT_EMAIL`，并按 [README](../README.md#隐私说明与个人信息申请) 完成一次收件与回复演练。若本次部署包含知识库所需的 `vector(1024)` 向量列，须在 `db-init` 之前按 [数据库准备说明](../ops/PGVECTOR.md) 在目标库启用扩展；该脚本不建库，账号与库需先按准备指南创建。在 `/opt/melonclaw/` 完成依赖安装后，以 `ubuntu` 账号安装模板并初始化全新数据库：

```bash
env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-resources --service-stopped install-builtins --source /opt/melonclaw/.data/skills/shared
env MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env /opt/melonclaw/.venv/bin/melonclaw-db-init
```

安装 [Web systemd 服务](../ops/systemd/melonclaw.service)、[Nginx 站点](../ops/nginx/melonclaw.conf) 和 [日志轮转配置](../ops/melonclaw.logrotate)。Nginx 配置中的域名、请求体上限与实际附件限制同步。站点 `server_name` 为 `melonclaw.cn www.melonclaw.cn`：80 端口返回 301，443 端口用 Let's Encrypt 证书托管 `frontend/dist`，并把 `/api/` 反代到本机 8000。`/.well-known/acme-challenge/` 留在 80 端口，供证书续期校验。页面走同源 `/api/`，`MELONCLAW_ALLOWED_ORIGINS` 仍填 `https://melonclaw.cn`。证书文件为 `/etc/letsencrypt/live/melonclaw.cn/fullchain.pem` 与同目录 `privkey.pem`，并且必须同时包含这两个主机名；文件存在后再安装站点，否则 `nginx -t` 失败。尚无证书时，先确认 80 端口空闲，执行 `sudo certbot certonly --standalone -d melonclaw.cn -d www.melonclaw.cn`，再安装下面的站点。已有只含主域名的证书时，在安装新站点前扩展原证书：`sudo certbot certonly --webroot -w /var/www/html --cert-name melonclaw.cn --expand -d melonclaw.cn -d www.melonclaw.cn`。续期沿用服务器上已有的 certbot 定时任务。站点内容以仓库配置为准；续期若改写了站点文件，重新安装该文件并 reload。完整签发与验收见 [首次上线流程](first-deployment.md) 第 7–9 节。

```bash
sudo install -m 0644 ops/nginx/melonclaw.conf /etc/nginx/sites-available/melonclaw
sudo ln -sfn /etc/nginx/sites-available/melonclaw /etc/nginx/sites-enabled/melonclaw
sudo rm -f /etc/nginx/sites-enabled/default   # 移除默认站点，避免 IP/未匹配 Host 命中欢迎页
sudo nginx -t && sudo systemctl reload nginx
sudo install -m 0644 ops/systemd/melonclaw.service /etc/systemd/system/melonclaw.service
sudo install -m 0644 ops/melonclaw.logrotate /etc/logrotate.d/melonclaw
sudo systemctl daemon-reload
sudo systemctl enable --now melonclaw
curl -sI http://melonclaw.cn | head
curl -fsS https://melonclaw.cn/api/ready
```

主域名 HTTP 响应为 301，`Location` 指向 `https://melonclaw.cn/`；`www` 的 `Location` 指向 `https://www.melonclaw.cn/`。两个 HTTPS 地址都能打开首页，就绪接口返回 `{"ready":true}`。安装时同时删除 `sites-enabled/default` 软链接（保留 `sites-available/default` 配置文件）：否则用 IP 或未匹配 Host 访问会命中 Ubuntu 默认站点，显示 “Welcome to nginx!”，而不是 melonclaw 页面。云安全组入站放行 TCP 80 和 443；后端只绑定 `127.0.0.1:8000`，不对公网开放。健康检查为 `/api/ready`。systemd 负责进程，不需要自行写 PID；文件日志采用 copytruncate 轮转，轮转瞬间有小量日志损失窗口，不把它当审计存储。安全审计在数据目录的 `audit/audit-YYYY-MM-DD.jsonl`，由应用至少保留 184 天，按文件日期清理，不走上述 14 天轮转。写入失败只记应用警告；如需通知安全负责人，在环境文件配置接收地址，并用本机测试接收端确认送达后再启用，见[安全审计日志](design-docs/security-audit-log.md)。站点配置会把访问者的源地址和源端口传给应用。更新 `ops/nginx/melonclaw.conf` 后重新安装并 reload；未重载时，审计里的来源是本机回环地址。

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

升级到 JWT 版本前，先在 Web 实际读取的外部环境文件填好随机 `MELONCLAW_JWT_SECRET`。如需自定义 `MELONCLAW_TOKEN_TTL_HOURS`，填不小于 1 的整数。常规发版脚本在停服前校验这两项配置。升级前记录当前 Git 提交并停止 Web 服务，备份一致的数据库、数据根和工作区。在 `/opt/melonclaw/` 执行 `git pull --ff-only`、`uv sync --locked --default-index https://pypi.org/simple`、`npm --prefix frontend ci` 和 `npm --prefix frontend run build`。Nginx 应使用 `ops/nginx/melonclaw.conf` 中对 `/index.html` 设置的 `Cache-Control: no-store`，使浏览器在发版后及时加载新入口与哈希资源；使用自定义配置时应保留该行为。若版本包含数据库升级，标准部署可直接运行 `uv run melonclaw-db-update`：命令会自动读取 `/etc/melonclaw/melonclaw.env`，与 systemd 服务使用相同配置。若配置文件放在其他位置，先设置 `MELONCLAW_ENV_FILE=/实际配置文件路径` 再运行。成功后重启 Web，检查包含任务执行器的 `/api/ready`。模板安装命令只填充缺失的 Skill，不更新已有同名 Skill；更新内置内容走资源管理流程。

回滚代码前确认数据库结构与旧版本兼容；切回已记录的旧 Git 提交后，重新同步依赖和构建前端，再启动服务。代码回滚不会自动回滚数据库 schema。异机备份需要包含 PostgreSQL、整个 data/workspaces 和受控保存的配置，定期演练恢复；数据库与磁盘应在同一停写窗口或一致快照中备份。

## 一次性账户清理

正式运营前可通过 [账户清理脚本](../ops/purge_legacy_accounts.py) 保留 `system/admin`，物理删除其他用户（含后台已软删除用户）和其他租户（含空租户、停用租户）。脚本使用部署环境中的 `DATABASE_URL`，要求目标库已应用当前版本的增量升级。

清理范围包括待删除用户的项目、会话、任务、知识库、私有配置、手机号验证记录、其提交的工单，以及可按已知用户、租户、会话或任务关联的 Memory 和 Checkpoint。属于待删除租户的任务也会移除。保留管理员正常使用的数据、全局配置、匿名工单和管理员提交的工单。保留资源中必须有用户引用的创建人或授权人字段会改为 `admin`，仍保留的知识库事件会清空已删除的操作人引用。磁盘上的工作区、附件、Skill、知识库原件、审计日志和已有备份继续保留。

1. 结束活动任务及知识库处理，执行 `sudo systemctl stop melonclaw`，并确认其他数据库写入已停。按上节备份数据库、数据目录、工作区及部署配置。
2. 在部署目录中，以运行 Web 的服务账号执行预览。若使用非标准环境文件，给以下两次命令设置相同的 `MELONCLAW_ENV_FILE`。

   ```bash
   uv run --locked python ops/purge_legacy_accounts.py
   ```

3. 核对数据库名以及各表影响行数，把下面的 `DATABASE_NAME` 替换为预览显示的库名。命令会检查 `system/admin`、尝试取得停服互斥锁，并在同一事务内清理和验证。事务出错时回滚，保持停服并重新预览。

   ```bash
   uv run --locked python ops/purge_legacy_accounts.py --apply --expect-database DATABASE_NAME
   uv run --locked python ops/purge_legacy_accounts.py
   ```

4. 确认第二次预览中 `users` 和 `tenants` 均为 0 行待删除。正式开放注册前，在部署配置中更换 `MELONCLAW_JWT_SECRET`，使旧令牌失效。旧令牌仅按用户名识别账户，用户名重新注册后仍可能匹配。对准备复用的用户名，将数据根下对应的 `skills/users/<用户 ID>/` 目录移出运行数据根，避免以后重建 Skill 索引时把旧文件关联给新账户。执行 `sudo systemctl start melonclaw`，检查 `/api/ready`，重新以管理员登录并检查用户管理列表。

## 短信验证

已有库停 Web 并备份后执行 `uv run melonclaw-db-update`。`20261126_sms_auth` 创建手机号绑定、验证请求和发送配额，`20261126_registration_verification` 创建注册核验记录表。升级保留已有用户、密码、租户和业务数据。注册核验留痕从升级后的自助注册开始。新空库仍用 `uv run melonclaw-db-init`。服务启动不建表。

默认 `MELONCLAW_SMS_ENABLED=false`、`MELONCLAW_REGISTRATION_ENABLED=false`，未填写阿里云凭据也可以启动，密码登录保持可用。启用前在 `/etc/melonclaw/melonclaw.env` 填写号码认证 AccessKey、赠送签名、登录/注册模板、绑定模板，以及至少 32 个字符且长期稳定的 `MELONCLAW_PHONE_HMAC_KEY`。密钥轮换需要单独迁移，不能靠重启重新生成。变量说明和真实短信联调见[短信注册与登录](design-docs/sms-registration-login.md)。

开放自助注册前关闭 dev 免密和身份切换，并更换种子管理员初始密码。短信关闭或云服务失败时，已创建的账号和绑定保留，用户继续使用密码登录。

## 投诉举报

已有库处理活动运行、停止 Web 并备份后执行 `uv run melonclaw-db-update`。步骤 `20261126_reports` 增加工单与操作历史，保留已有业务数据，重复执行会跳过。启动后核对 `/api/ready` 以及登录页「意见反馈」。新空库使用 `uv run melonclaw-db-init`。

在服务读取的环境文件中填写实际值守邮箱、可选联系人及可兑现的响应时限，重启 Web 后从登录页核对。反代使用仓库 Nginx 配置覆盖 `X-Real-IP` 并保持后端仅绑定回环地址。安装或更新反代配置后重载 Nginx。站内反馈需要管理员进入系统管理处理，邮箱和时限属于对外展示配置。[投诉举报与反馈](design-docs/reports.md)

## 安全自评验收

正式提交安全自评前，按[安全自评与监管协查](design-docs/security-assessment-operations.md)核对外网请求审计、短信核验覆盖范围、投诉办结及模拟协查。

## 当前验证边界

本机已执行文件迁移、数据库相对路径及 Skill 正文核对；2026-10-07 远程 Ubuntu 已确认 systemd 后端运行、本机 Nginx API／首页可访问、证书签发部署成功及 HTTPS 登录页可见。仓库中的 Nginx 配置现已同时包含 HTTP 301 和 HTTPS 站点；生产机安装该文件并 reload 后，再用公网 HTTP 301 与 HTTPS `/api/ready` 验收。证书续期测试、日志轮转和完整业务场景仍需验收，详见 [首次上线记录](first-deployment.md#10-使用验收和当前结论)。目录分离不提供多用户 Shell 隔离：当前 LocalShellBackend 不是安全沙箱，共享远程部署仍需解决身份、执行隔离和授权边界后再开放给不可信用户。

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

## 向量知识库首次上生产

适用于首次把当前知识库结构接入生产。先确认发布提交包含后端、前端及依赖锁文件。已有用户和会话的库走 `melonclaw-db-update`；空库走 `melonclaw-db-init`。两条命令都不清库。已有知识库表与当前结构不一致时升级失败并回滚，不重建、不删表。

1. 结束活动定时任务和资料处理，停止所有 Web 实例。记录旧提交，在同一停写窗口备份 PostgreSQL、data、workspaces 和外部配置。
2. 按发版手册拉取发布提交、同步锁定依赖并重新构建前端。保持 Web 停止，完成空库初始化后再启动。
3. 在 PostgreSQL 服务端准备 pgvector 并在目标生产库启用。远程或托管数据库由数据库管理员完成这一步。标准部署按 [pgvector 准备说明](../ops/PGVECTOR.md#3-已有部署升级生产) 检查目标库，确认支持 `vector(1024)`。
4. 在 `/etc/melonclaw/melonclaw.env` 配置固定嵌入的 `KNOWLEDGE_EMBEDDING_API_KEY`、`KNOWLEDGE_EMBEDDING_URL`、`KNOWLEDGE_EMBEDDING_MODEL_NAME` 和 `KNOWLEDGE_EMBEDDING_DIMENSIONS=1024`。除 TXT 与 Markdown 外，知识库原件整份发往 DeepSeek 官方 OCR `https://api.deepseek.com/v1/tools/ocr`，凭据复用 `DEEPSEEK_API_KEY`。`KNOWLEDGE_OCR_TIMEOUT_SECONDS` 默认 180 秒，覆盖单次请求的上传和完整响应读取。保活换行不会延长时限。按成功识别页计费；客户端超时后，远端仍可能完成并计费。部署模板见 [ops/melonclaw.env.example](../ops/melonclaw.env.example)。环境文件中的 `KNOWLEDGE_OCR_API_KEY`、`KNOWLEDGE_OCR_URL`，以及更早的 `KNOWLEDGE_OCR_MODEL_NAME`、`KNOWLEDGE_OCR_MAX_OUTPUT_TOKENS` 可以删除。确保服务账号可写 `MELONCLAW_DATA_DIR`，并将其下的 `knowledge/` 纳入日常备份。
5. 在发布目录以部署账号建表，成功后再启动单个 Web 实例。已有用户和会话时执行升级，不要执行 `melonclaw-db-init`：

   ```bash
   cd /opt/melonclaw
   MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env .venv/bin/melonclaw-db-update
   sudo systemctl start melonclaw
   curl --fail --show-error http://127.0.0.1:8000/api/ready
   ```

   `20261117_knowledge` 创建当前知识库八张表和检索视图，不改已有业务表。`20261117_model_purpose` 给已有模型补上 `purpose=chat`，放宽上下文窗口的非空限制，并按用途重建默认模型唯一索引。`20261117_parser_model` 给知识库补上可空的 `parser_model_id` 及其外键和索引，与本地库一致；已有该列时不改数据。`20261126_knowledge_moderation` 扩展知识库事件的操作类型，允许记录停用和恢复；不改已有事件，不删除文档或版本。重复执行会跳过。失败时保持 Web 停止。空库仍使用 `melonclaw-db-init`，它会建出同一套结构并登记这些升级。

6. 用可公开的测试资料建一个私库，上传 TXT 并等到可检索，分别用向量和关键词查找预期片段。再在普通聊天中选库提问，点击引用确认能打开原文，换未授权账户确认不能访问。需要支持扫描件时，再上传扫描 PDF 或图片验证真实 OCR。就绪探测通过后仍需完成这些验收。
7. 如需回滚，先停服。旧代码与升级后数据库不兼容时，从同一停写窗口的备份恢复数据库及与其对应的文件，并重新构建旧版前端。恢复该备份会丢失备份之后的生产写入。
