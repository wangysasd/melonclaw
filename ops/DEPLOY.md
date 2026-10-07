# MelonClaw 重新发版操作手册

## 1. 适用范围

本手册用于更新已部署的 Ubuntu 系统。
首次安装请使用[首次上线流程](../docs/first-deployment.md)。

| 项目 | 配置 |
|---|---|
| 部署和运行账号 | `ubuntu` |
| 代码目录 | `/opt/melonclaw/` |
| 配置文件 | `/etc/melonclaw/melonclaw.env` |
| Skill 数据目录 | `/var/lib/melonclaw/data/` |
| 工作区目录 | `/var/lib/melonclaw/workspaces/` |
| 应用服务名称 | `melonclaw` |
| 网站地址 | `https://melonclaw.cn` |

本手册由英文版翻译而来，沿用 [ASD-STE100](https://www.asd-ste100.org/STE_faq.html) 的简明写作原则。
使用短句、主动表达和直接指令。
ASD-STE100 是英语规范，中文译文不声明符合其英语词汇规则。
产品名称、命令、路径和软件术语保留原文。

## 常规发版：一条命令

不涉及数据库升级、生产配置或服务模板变更时，在服务器以 `ubuntu` 执行：

```bash
cd /opt/melonclaw
bash ops/deploy.sh
```

首次使用前先拉取包含该脚本的版本。不要用 sudo 运行整个脚本。
脚本只对 systemd 命令使用 sudo，需要时会提示管理员密码。
执行前确认版本无需数据库升级，并按部署维护流程准备所需备份。

脚本先检查依赖命令、配置读取权限和代码目录状态，并阻止重复运行。
预检查通过后依次停止后端、快进拉取代码、同步 Python 依赖、安装前端依赖、构建前端、启动后端。
最后检查后端进程和就绪接口，确认返回 `ready=true`。

预检查失败不会停止服务。停服后发生错误，后端保持停止，不自动回滚代码或前端文件。
前端直接在现有 dist 目录构建，失败可能影响网页访问。
脚本不执行数据库初始化或升级，不修改外部配置，不覆盖或重新加载 Nginx。
后端使用非默认端口时，在执行前设置 `MELONCLAW_READY_URL` 为实际的本机就绪接口地址。
脚本成功后，仍需验证公网访问、聊天和本次变更功能。

## 2. 选择更新流程

| 变更内容 | 使用流程 |
|---|---|
| 仅修改前端代码或前端依赖 | 第 3 节 |
| 修改后端代码、Python 依赖或应用配置 | 第 4 节 |
| 同时修改前端和后端 | 第 4 节 |
| 修改数据库结构 | 第 4 节，包含第 13 步 |

无法确定变更类型时，使用第 4 节。

## 3. 仅更新前端

前端构建会直接写入 Nginx 正在提供服务的目录。
构建期间可能短暂影响网页访问。

1. 在开发电脑完成前端测试。
2. 将确认发布的修改推送到 GitHub。
3. 使用 `ubuntu` 账号连接服务器。
4. 进入代码目录。

   ```bash
   cd /opt/melonclaw
   ```

5. 拉取新代码。

   ```bash
   git pull
   ```

   如果 Git 提示冲突，停止更新。
   未核对前，不要丢弃服务器上的修改。

6. 按锁文件安装前端依赖。

   ```bash
   npm --prefix frontend ci
   ```

7. 构建前端。

   ```bash
   npm --prefix frontend run build
   ```

   确认命令执行成功。
   构建失败时，按第 6 节处理。

8. 在浏览器打开 `https://melonclaw.cn`。
9. 刷新页面。
10. 验证本次修改的功能。

Nginx 从 `/opt/melonclaw/frontend/dist/` 读取新文件。
仅更新前端时，不需要重启后端服务。
未修改 Nginx 配置时，不需要重新加载 Nginx。

## 4. 更新后端或完整应用

1. 在开发电脑完成所需测试。
2. 将确认发布的修改推送到 GitHub。
3. 使用 `ubuntu` 账号连接服务器。
4. 进入代码目录。

   ```bash
   cd /opt/melonclaw
   ```

5. 停止应用服务。

   ```bash
   sudo systemctl stop melonclaw
   ```

   确认应用任务已停止。

6. 按数据库备份流程备份数据库。
7. 备份外部数据和配置目录。

   备份应包含 `/var/lib/melonclaw/` 和 `/etc/melonclaw/`。
   将备份保存在代码目录之外。
   配置备份包含凭据，应限制访问权限。

8. 拉取新代码。

   ```bash
   git pull
   ```

   如果 Git 提示冲突，停止更新。

9. 按锁文件安装 Python 依赖。

   ```bash
   uv sync --locked --default-index https://pypi.org/simple
   ```

   如果命令提示锁文件错误，停止更新。
   不要在服务器重新生成锁文件。

10. 如果本次修改了前端，安装前端依赖。

    ```bash
    npm --prefix frontend ci
    ```

11. 如果本次修改了前端，构建前端。

    ```bash
    npm --prefix frontend run build
    ```

12. 如果本次新增了配置变量，编辑外部配置文件。

    ```bash
    sudo nano /etc/melonclaw/melonclaw.env
    ```

    保留已有凭据和外部数据路径。
    必要时，将 Skill 所需变量加入环境变量白名单。
    配置说明见 [README](../README.md)。

13. 如果本次包含数据库升级，执行升级命令。

    ```bash
    MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env \
      .venv/bin/melonclaw-db-update
    ```

    升级失败时，保持应用服务停止。
    不要使用 `melonclaw-db-init` 进行版本升级。
    不要删除数据库数据。

14. 如果本次修改了 systemd 服务文件，安装新文件。

    ```bash
    sudo install -m 0644 ops/systemd/melonclaw.service \
      /etc/systemd/system/melonclaw.service
    sudo systemctl daemon-reload
    ```

15. 如果本次修改了日志轮转文件，安装新文件。

    ```bash
    sudo install -m 0644 ops/melonclaw.logrotate \
      /etc/logrotate.d/melonclaw
    ```

16. 启动应用服务。

    ```bash
    sudo systemctl start melonclaw
    ```

17. 检查应用服务状态。

    ```bash
    sudo systemctl status melonclaw --no-pager
    ```

    确认状态显示 `active (running)`。

18. 检查本机后端是否就绪。

    ```bash
    curl -fsS http://127.0.0.1:8000/api/ready
    ```

    如果服务仍在启动，稍等后重新检查。
    确认返回 `{"ready":true}`。

19. 检查公网 HTTPS 连接。

    ```bash
    curl -fsS https://melonclaw.cn/api/ready
    ```

    确认返回 `{"ready":true}`。

20. 完成第 5 节的功能检查。

## 5. 验证发版结果

1. 打开 `https://melonclaw.cn`。
2. 使用已有账号登录。
3. 发送一条测试消息。
4. 确认回复显示正常。
5. 打开已有会话。
6. 下载一个已有文件。
7. 验证本次修改的功能。

浏览器缓存未更新时，Windows 使用 Ctrl+Shift+R，macOS 使用 Command+Shift+R 强制刷新。
就绪接口正常不代表全部业务功能正常。

不要用仓库模板覆盖当前 Nginx 站点文件。
Certbot 已在当前站点中加入 HTTPS 配置。
如果本次需要修改 Nginx，保留证书配置。
重新加载前，检查配置。

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 6. 停止条件与恢复

任何命令失败时，不要继续执行下一步。
后端更新失败时，保持应用服务停止，直到问题解决。
前端构建失败时，网站目录可能包含不完整的构建文件。
恢复已验证可用的前端构建，或修复构建错误。

出现应用错误时，检查 `/var/log/melonclaw/backend.log`。
分享日志前，删除或脱敏凭据。

回滚代码前，确认数据库与旧版本兼容。
代码回滚不会撤销数据库升级。
回滚后，重新安装旧版本依赖。
必要时，重新构建前端。
恢复后，完成第 5 节的功能检查。

## 7. 更新前的可选检查

检查服务器是否有本地修改：

```bash
git status --short
```

记录更新前的代码版本：

```bash
git rev-parse HEAD
```

这两条命令不参与实际安装。
记录提交号可以帮助定位需要恢复的旧版本。
如果希望在分支分叉时停止拉取，用 `git pull --ff-only` 代替 `git pull`。

## 7.自己的笔记
cd /opt/melonclaw
sudo systemctl stop melonclaw
git pull
npm --prefix frontend ci
npm --prefix frontend run build
sudo systemctl start melonclaw
sudo systemctl status melonclaw --no-pager
