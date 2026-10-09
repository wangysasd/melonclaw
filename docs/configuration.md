# 配置与手动启动

首次使用请先按[本地快速启动](../README.md#本地快速启动)安装依赖并初始化数据库。本文补充可选配置和手动启动方式。

## 数据库连接

根目录 `.env` 中的 `DATABASE_URL` 必须指向已创建的 PostgreSQL 数据库，应用账号需要连接和建表权限。连接格式见 [.env.example](../.env.example)；用户名或密码中的 URL 特殊字符需百分号编码。

例如，本地 PostgreSQL 认证配置完成后，可创建数据库：

```bash
createdb melonclaw
```

首次建库执行 `uv run melonclaw-db-init`，保留历史数据的升级执行 `uv run melonclaw-db-update`。服务启动不会自动建表或升级；不要用反复初始化来排查连接问题。

## 可选环境变量

后端默认读取根目录 `.env`，已导出的同名环境变量优先。完整配置项见 [.env.example](../.env.example)。不要提交 `.env`，也不要在日志、截图或问题反馈中公开实际连接串、Key、Token。

| 变量 | 用途与默认值 |
|---|---|
| `TAVILY_API_KEY` | 内置联网搜索凭据；留空不影响启动，依赖该凭据的搜索能力不可用 |
| `MELONCLAW_DATA_DIR` | Skill 等持久资源目录，默认 `~/.melonclaw/data` |
| `MELONCLAW_WORKSPACE_DIR` | 会话与项目文件目录，默认 `~/.melonclaw/workspaces` |
| `profile` | 仅 `dev` 开启免密登录和全员身份切换；直接启动后端时按此值生效 |

持久数据目录和工作区必须位于项目代码目录之外。仓库里的内置 Skill 是分发模板，安装后使用外部数据目录中的运行时副本。

模型名称、服务地址和 API Key 在「拓展 → 模型」配置。初始化只写入未启用、无凭据的供应商模板，不创建默认模型，也不从环境变量自动生成模型。无需 MCP 时可不创建 `mcp.json`。

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
