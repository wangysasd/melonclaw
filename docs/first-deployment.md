# Ubuntu 首次上线流程与操作说明

记录日期：2026-10-07。本文根据本次操作整理，使用 `ubuntu` 账号，代码固定在 `/opt/melonclaw/`，域名为 `melonclaw.cn`。这是首次部署流程，不应在每次升级时从头执行。命令中的仓库、域名和目录在迁移到其他环境时需同步调整。

## 1. 最终部署结构

| 位置 | 用途 |
|---|---|
| `/opt/melonclaw/` | Git 代码、Python 虚拟环境、前端构建文件、内置 Skill 模板 |
| `/var/lib/melonclaw/data/` | 运行时共享和个人 Skill、导入草稿、操作恢复记录 |
| `/var/lib/melonclaw/workspaces/` | 项目／会话生成文件、附件及解析内容 |
| `/etc/melonclaw/melonclaw.env` | 数据库连接、凭据、目录和运行参数 |
| `/var/log/melonclaw/backend.log` | 后端日志 |
| `/run/melonclaw/` | systemd 管理的临时运行目录 |
| PostgreSQL | 用户、会话、资源索引、Checkpoint、长期 Memory |

访问链路为：浏览器 HTTPS → Nginx → 前端静态文件，或 `/api/` 反向代理 → 本机后端 8000 → PostgreSQL／持久目录。用户数据独立于代码，后续 git pull 不应覆盖数据目录。

## 2. 取得仓库代码与 GitHub 访问权限

前提：Python、uv、Node.js/npm、Git 已可用，服务器能连接 PostgreSQL。安装这些基础软件及创建数据库的具体过程未在本次对话中完整记录。

在 `/opt` 下克隆，Git 自动创建 `melonclaw` 子目录：

```bash
cd /opt
git clone git@github.com:wangysasd/melonclaw.git
cd /opt/melonclaw
```

`/opt` 需要允许 ubuntu 创建仓库目录，可由管理员预先创建 `/opt/melonclaw` 并交给 ubuntu。代码、虚拟环境应由 ubuntu 维护，不用 sudo 执行 npm 或 uv 安装。

本次切换 SSH 地址后遇到 `Permission denied (publickey)`，原因是服务器公钥没有仓库访问权限。处理过程如下，已有密钥时先检查，不覆盖：

```bash
ls ~/.ssh/*.pub
# 仅在没有此密钥时创建；按提示操作
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "melonclaw-server"
cat ~/.ssh/id_ed25519.pub
```

将完整公钥加入 GitHub 仓库 **Settings → Deploy keys → Add deploy key**；Title 用 `melonclaw-server`，Key 为 `.pub` 全文，不勾选写权限。私钥 `id_ed25519` 不上传、不粘贴到聊天。首次连接出现主机指纹提示时，应先核对 GitHub 官方指纹。

```bash
ssh -T git@github.com
cd /opt/melonclaw
git remote set-url origin git@github.com:wangysasd/melonclaw.git
git pull --ff-only
```

`ssh -T` 的成功标志是 GitHub 身份验证成功提示，不是获得远程 Shell；GitHub 不提供 Shell。`git pull --ff-only` 只接受快进更新，避免在部署机产生合并提交。本次已成功拉取 main。

## 3. 安装依赖并构建网页

```bash
cd /opt/melonclaw
uv sync --locked --default-index https://pypi.org/simple
npm --prefix frontend ci
npm --prefix frontend run build
```

- `uv sync` 根据 `uv.lock` 安装后端依赖到 `.venv`；`--locked` 禁止重新解析并改写锁文件。
- `npm --prefix frontend ci` 在 frontend 目录按 package-lock 安装前端依赖。
- `npm --prefix frontend run build` 检查 TypeScript 并构建网页到 `frontend/dist/`，供 Nginx 读取；生产不启动 Vite 开发服务器。

本次服务器默认镜像导致 uv.lock 下载源被改为腾讯云镜像，格式 revision 也改变。确认差异后备份并恢复锁文件：

```bash
cp uv.lock ~/melonclaw-uv.lock.backup
git restore -- uv.lock
```

即使仓库文件没有本地修改，使用默认镜像仍曾触发“lockfile needs to be updated”。显式指定 PyPI 后命令成功。后续 uv 命令使用相同源；如果官方源网络不可达，先排查网络或制定统一镜像方案，不直接删除 `--locked`。

前端本次构建成功。500 kB chunk 提示是体积警告，不是构建失败。`npm audit` 则报告了 13 个漏洞，涉及测试／构建工具以及网页运行依赖；本次未修复。不要在服务器直接 `npm audit fix --force`，应在开发仓库修复、测试并提交锁文件后统一部署。构建成功不代表安全问题已解决。

## 4. 创建外部目录和配置文件

使用现有 ubuntu，不额外创建 melonclaw Linux 账号。以下命令设置数据和配置的访问权限，并避免覆盖已有配置：

```bash
cd /opt/melonclaw
sudo install -d -o ubuntu -g ubuntu -m 0700 \
  /var/lib/melonclaw \
  /var/lib/melonclaw/data \
  /var/lib/melonclaw/workspaces \
  /var/log/melonclaw \
  /etc/melonclaw

if ! sudo test -e /etc/melonclaw/melonclaw.env; then
  sudo install -o ubuntu -g ubuntu -m 0600 \
    ops/melonclaw.env.example /etc/melonclaw/melonclaw.env
fi
sudo nano /etc/melonclaw/melonclaw.env
```

`install -d` 创建目录并设置属主／权限；0700 只允许属主访问，配置 0600 只允许属主读写。Nano 保存是 Ctrl+O、回车，退出是 Ctrl+X。

填写数据库连接和所需凭据，保留这些非敏感配置：

```dotenv
profile=prod
MELONCLAW_DATA_DIR=/var/lib/melonclaw/data
MELONCLAW_WORKSPACE_DIR=/var/lib/melonclaw/workspaces
MELONCLAW_HOST=127.0.0.1
MELONCLAW_PORT=8000
MELONCLAW_ALLOWED_ORIGINS=https://melonclaw.cn
```

`DATABASE_URL` 必须指向已创建的数据库；不要输出实际连接串或密钥。Origin 是浏览器访问的协议、域名和可选端口，不带路径或结尾 `/`。本次域名先误写为 .com，最终统一为 .cn。仅填写此变量不会配置 DNS 或证书。

## 5. 安装内置 Skill 并初始化全新数据库

在服务尚未启动、没有写入任务时运行：

```bash
cd /opt/melonclaw
export MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env

uv run --locked --default-index https://pypi.org/simple \
  melonclaw-resources --service-stopped install-builtins \
  --source .data/skills/shared

uv run --locked --default-index https://pypi.org/simple \
  melonclaw-db-init
```

第一个命令把仓库内置模板安装到外部数据根。已有同名 Skill 整体跳过；本次输出“已安装 0 个”，表示目标已存在，不是安装失败。`--service-stopped` 是操作者的停服声明，命令不自动停止服务。

第二个命令创建完整业务表、Checkpoint 和 Memory Store，写入初始账户、供应商模板和资源索引。数据库只索引外部 Skill 运行目录，路径保存为相对路径。服务启动不会自动建表。

这一步用于新库；已有历史数据的部署按版本要求执行 `melonclaw-db-update`，不能为升级清库。本次用户确认数据库已创建，但没有贴出 db-init 完整执行结果；后续 ready=true 证明服务要求的 schema 已存在且校验通过，不据此推断全部业务功能已验收。

## 6. 安装 systemd 服务和日志轮转

```bash
cd /opt/melonclaw
sudo install -m 0644 ops/systemd/melonclaw.service \
  /etc/systemd/system/melonclaw.service
sudo install -m 0644 ops/melonclaw.logrotate \
  /etc/logrotate.d/melonclaw
sudo systemctl daemon-reload
sudo systemctl enable --now melonclaw
```

systemd 是 Linux 的服务管理器。配置文件中 `User=ubuntu`、`Group=ubuntu` 决定运行账号，`WorkingDirectory=/opt/melonclaw` 决定工作目录，`Environment=MELONCLAW_ENV_FILE=...` 告诉后端读取哪个配置文件，`ExecStart=/opt/melonclaw/.venv/bin/melonclaw-web` 指定实际启动入口。

`daemon-reload` 让 systemd 重新读取服务配置；`enable --now` 设置开机启动并立即启动。以后 `sudo systemctl restart melonclaw` 就会按这个文件重启后端，读取更新后的配置。修改 service 文件后还要先 daemon-reload，修改 env 文件只需重启后端。

```bash
sudo systemctl status melonclaw --no-pager
curl -fsS http://127.0.0.1:8000/api/ready
```

本次确认服务 active (running)，接口返回 `{"ready":true}`。日志模板保存后端文件日志并按策略轮转，实际轮转效果仍需验收。

## 7. 配置 Nginx 提供网页和代理 API

本次 Nginx 已安装，版本为 Ubuntu nginx/1.24.0，最初只启用了 default 站点。首次新增应用站点：

```bash
sudo cp /opt/melonclaw/ops/nginx/melonclaw.conf \
  /etc/nginx/sites-available/melonclaw
sudo nano /etc/nginx/sites-available/melonclaw
```

将 `server_name` 改为 `melonclaw.cn`；模板的网页根为 `/opt/melonclaw/frontend/dist`，`/api/` 转发到 `127.0.0.1:8000`。关闭代理缓冲让聊天 SSE 增量及时显示。

`/etc/nginx/sites-available/melonclaw` 是配置文件，不能 cd 进去。启用链接只针对 Nginx 站点文件，与代码目录无软链接的方案不冲突：

```bash
sudo ln -sfn /etc/nginx/sites-available/melonclaw \
  /etc/nginx/sites-enabled/melonclaw
sudo nginx -t && sudo systemctl reload nginx

curl -fsS -H 'Host: melonclaw.cn' http://127.0.0.1/api/ready
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Host: melonclaw.cn' http://127.0.0.1/
```

`nginx -t` 检查配置，成功才 reload；Host 请求头用于在本机选中域名站点。本次分别得到 ready=true 和 HTTP 200，证明反向代理与静态首页都可访问。此时还只有 HTTP，不应把它当 HTTPS 上线完成。

## 8. 配置 DNS 和网络入口

在域名 DNS 管理平台添加 A 记录：类型 A，主机记录 `@`，值为服务器公网 IPv4。`@` 代表根域名 melonclaw.cn。

```bash
dig +short melonclaw.cn A
sudo ufw status
```

本次 dig 最初没有输出，之后返回了公网 IP；应与云控制台这台服务器的公网 IP 核对。UFW 返回 inactive，未启用本机 UFW，不需要另加规则；这不代表云安全组自动放行。

云安全组入站允许 TCP 80 和 443，公网网站通常来源为 `0.0.0.0/0`。后端绑定本机地址，8000 不对公网开放。如 UFW 已启用，再按该机策略放行 80/443。若配置了 IPv6 AAAA，也应确认它指向可达的正确服务器。

## 9. 申请并启用 HTTPS

DNS 和公网 80 已可达后：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d melonclaw.cn --redirect
```

Certbot 验证域名控制权、申请证书并修改 Nginx 配置；`--redirect` 将 HTTP 重定向到 HTTPS。按提示填写邮箱和同意条款。

本次 Certbot 显示签发和部署成功，证书位于 `/etc/letsencrypt/live/melonclaw.cn/fullchain.pem`，私钥为同目录 `privkey.pem`，当次证书有效期至 2027-01-05，并设置了自动续期任务。这个日期仅是本次记录，续期后会变化；私钥不得泄露。

```bash
curl -fsS https://melonclaw.cn/api/ready
sudo certbot renew --dry-run
```

这两条用于验证公网 HTTPS API 和续期链路；本次对话未提供执行结果，仍应完成。浏览器已经展示 HTTPS 登录页。之后不要再直接 cp 仓库 HTTP 模板覆盖现有站点，否则可能丢失 Certbot 添加的 HTTPS 配置。

## 10. 使用验收和当前结论

本次已确认：Git 拉取成功、前端构建成功、后端运行且就绪、本机 Nginx API／首页通过、DNS 返回地址、证书签发部署成功、浏览器 HTTPS 登录页可见。

以下事项尚未提供完成证据：

- 管理员登录并修改初始密码；初始账户为 admin，首次密码为 admin。
- 配置模型后正常聊天，验证 SSE 流式回复。
- 上传附件、生成和下载文件；重启后历史记录、文件和 Skill 可用。
- Memory 写入／读取、会话恢复的真实业务验收。
- HTTPS API 检查、证书续期 dry-run、日志轮转和服务器重启后的自动启动验证。
- 修复 npm audit 报告的依赖漏洞，配置数据库及文件的异机备份并验证恢复。

所以基础访问链路已部署成功，不能把“看到登录页”当作全部生产验收完成。当前 LocalShellBackend 没有安全沙箱，ubuntu 同时部署和运行意味着应用 Shell 具有该账号的文件权限，面向不可信用户开放仍需解决执行隔离与授权边界。

## 11. 以后升级与排障

以后不重复执行首次建库或覆盖 Nginx 站点。升级前记录提交、停服并备份数据库和外部文件；git pull 后同步依赖和构建，按需 db-update，再启动并验收。详细流程及回滚限制见 [部署目录与维护说明](deployment.md)。

常用命令：

```bash
sudo systemctl restart melonclaw
sudo systemctl status melonclaw --no-pager
sudo nginx -t
curl -fsS http://127.0.0.1:8000/api/ready
```

后端日志在 `/var/log/melonclaw/backend.log`；读取、分享日志前检查并脱敏凭据。排障时优先确认外部配置、目录权限、数据库、后端就绪和 Nginx，逐层定位，不通过反复初始化数据库来修复连接问题。
