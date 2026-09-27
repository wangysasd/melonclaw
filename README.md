# MelonClaw

<p align="center">
  <img src="frontend/public/assets/brand/melon-claw.png" alt="MelonClaw" width="314" height="314" />
</p>

基于 Deep Agents 的通用 AI 助手：在 Web 界面完成问答、写作、研究和计划制定，所有副作用默认受控。

## ✨ 为什么用 MelonClaw

- ✅ **可控副作用**：写文件、删文件、Shell 执行前需批准，可改参或拒绝。
- 🙋 **主动提问**：关键信息缺失时暂停并给出结构化选项，提交后继续。
- 📁 **工作区隔离**：普通会话各用独立目录，Project 内会话共享项目目录。
- 🤝 **协作与计算**：子 Agent 委派独立任务；纯计算走 QuickJS，无文件网络权限。
- 💾 **长期记忆**：PostgreSQL 保存业务数据、对话 Checkpoint 和多级 Memory。
- 🎛️ **多模型+流式**：发送按钮旁切换本轮模型，实时展示工具调用和审批过程；支持管理员发布共享自定义模型、用户配置私有模型（OpenAI 兼容接口）。

## 📋 环境要求

- Python 3.11+、`uv`、PostgreSQL 14+
- 一个模型 API（默认 DeepSeek，兼容 OpenAI 接口）
- Node.js 20+（`scripts/start.sh` 一键启动需要；只调后端可免）

## 🚀 快速开始

### 1. 获取代码

```bash
git clone <your-repository-url>
cd melonclaw
cp .env.example .env
```

### 2. 填必填配置

数据库需提前创建：

```bash
createdb melonclaw
```

`.env` 最小可用示例：

```dotenv
DATABASE_URL=postgresql+asyncpg://用户名:密码@127.0.0.1:5432/melonclaw
```

模型目录以数据库为唯一事实来源，分两步配置：**先配置模型供应商，再进供应商配置模型**。`melonclaw-db-init` 只写入 23 家未启用、无凭据的供应商模板，不创建任何模型，也不读取模型环境变量。之后通过资源管理界面的「模型」页维护：只有管理员可以新增、修改和删除供应商，供应商统一为全局共享；管理员配置的模型所有用户都可以使用，在聊天模型下拉中统一归入「内置模型」；普通用户的个人模型归入「自定义模型」，模型名称后不展示共享标签。普通用户可在已有供应商上配置自己的 API Key 并添加个人模型，不能新增供应商。已启用供应商在 Base URL 下方显示启用模型名称，超长单行省略，鼠标悬停可查看完整列表。未启用供应商以紧凑卡片展示，点击卡片可打开配置。供应商页的「已启用」对管理员按供应商开关分组，对普通用户仅按是否配置了自己的 Key 分组；未配置个人 Key 不影响使用管理员共享的模型。普通用户还可以在共享供应商上设置自己的 Key（`PUT/DELETE /api/model-providers/{key}/my-key`，调用时优先于共享 Key）。`.env` 不需要模型 Key 或模型名称。登录后配置供应商 Key 并添加模型；无可用模型时仍能启动和管理配置，但不能发送聊天消息。

可选：`TAVILY_API_KEY`（联网搜索用，不配也能启动）。

### 3. 安装并建表

```bash
uv sync --locked
uv run melonclaw-db-init
```

> 服务启动不建表、不迁移。改表结构后清空数据库再执行本命令。

每个用户在数据库中固定归属一个租户。初次上线只有一个用户：`system` 租户下的 `admin`（管理员）；通过页面创建的新用户默认归属 `system` 租户。当前开发数据库中，李四归属「市场部」（租户 ID `market`），张三归属「研发部」（租户 ID `research`），管理员仍归属「系统」（租户 ID `system`）。租户名称和 ID 由 `repository/seed_data.py` 的租户种子维护，用户的实际归属保存在 `users.tenant_id`；页面只选择模拟用户，租户、租户角色和状态由服务端从数据库读取。调整用户归属时，需在事务中确保新租户存在并更新用户的 `tenant_id`，同时维护租户种子。清空开发数据库时，也会清空用户与租户记录；重建后重新创建非种子用户并设置归属。

### 4. 启动

```bash
scripts/start.sh      # 前端 + 后端
scripts/restart.sh    # 重启
scripts/shutdown.sh   # 停止
```

- 前端：`http://127.0.0.1:8001`，后端：`http://127.0.0.1:8000`
- 端口占用时脚本拒绝启动，先跑 `scripts/shutdown.sh`
- 验证：`curl http://127.0.0.1:8000/api/status` 返回 `"ready"`
- 发一条消息能正常回复即跑通
- 管理 Skill：打开侧栏「插件」→「Skills」，可搜索、远程安装或上传；卡片右上角「添加」按权限启用技能，「使用」会新建一个已选中该技能的对话；「…」菜单可下载或删除，删除前需要确认。

## ⚙️ 必要文件

| 文件 | 作用 | 说明 |
| --- | --- | --- |
| `.env` | 全部凭据和地址 | 从 `.env.example` 复制，不提交 Git；只读环境变量 |
| `mcp.json` | 内置 MCP 种子 | `melonclaw-db-init` 时单向同步进数据库（不覆盖已有行的运营字段）；运行时装配只读数据库 |
| 工作区目录 | 会话文件和附件 | 默认 `~/.melonclaw/workspaces`，可用 `MELONCLAW_WORKSPACE_DIR` 修改 |
| `.data/` 数据根 | Skill 资源正文 | 系统共享与用户私有 Skill 的唯一存储；默认仓库下 `.data/`，可用 `MELONCLAW_DATA_DIR` 修改；`skills/shared/` 纳入版本控制（`cicc-*`/`htsc-*` 除外），日常管理走资源管理 UI |

常用可选变量：`MELONCLAW_HOST` / `MELONCLAW_PORT` / `MELONCLAW_FRONTEND_*`（改监听地址）、`MELONCLAW_WORKSPACE_DIR`（换工作区）、`MELONCLAW_DATA_DIR`（换 Skill/MCP 资源数据根，默认仓库下 `.data/`）。

## ⚠️ 排障与边界

排障：

- 端口被占用 → `scripts/shutdown.sh` 后再启动
- 启动提示表结构不一致 → 备份后清空数据库，重跑 `uv run melonclaw-db-init`
- 模型下拉为空 → 确认已运行 `uv run melonclaw-db-init`，并在资源管理界面检查模型是否已配置 API Key

边界：

- `.env` 只放本地，不提交。
- 页面用户是开发模拟身份，不是生产认证；同租户用户也不能访问彼此的项目、会话或附件。
- `LocalShellBackend` 不是沙箱，仅限本机 `127.0.0.1` 单用户开发使用。

### 供应商高级配置

在插件 → 模型页点击卡片，管理员可通过双列表单编辑供应商；点击「确定」保存配置，供应商启用状态按表单中的状态开关保存。高级配置默认收起，点击展开后支持请求头 JSON（只写不回显，留空保留，`{}` 清空）与供应商扩展请求体 JSON（不可覆盖模型、消息、工具、流式协议或写入凭据）。API Key Env 填大写且以 `_API_KEY` / `_ACCESS_TOKEN` 结尾的变量名；个人 Key、数据库共享 Key 均未设置时才使用该环境变量。修改环境变量后重启服务。Provider Type 当前只支持 OpenAI Completions API。

本次新增模型供应商表字段，旧开发数据库需要清空模型相关表后执行 `uv run melonclaw-db-init`，再运行 `scripts/restart.sh`；该操作会重置模型配置和个人 Key。详见 [供应商设计](docs/design-docs/custom-models.md)。


普通用户配置个人 Key 后可独立添加和使用个人模型，不受供应商全局 enabled 开关影响；管理员开关仅控制内置模型。个人模型仅使用个人 Key，清除后不可用，不借用共享 Key。连接地址仍由管理员维护。

管理员停用供应商并保存时，会清除数据库共享 Key 和 API Key Env 引用；不会修改部署环境变量或各用户的个人 Key。重新启用需重新配置共享凭据。

默认模型按归属独立保存：全局一个、每个用户一个个人默认。可用的个人默认优先于管理员内置默认；个人默认不可用时使用内置默认，两者都不可用时优先首个可用内置模型，再选个人模型。配置刷新时聊天选择同步服务端默认，用户仍可在聊天中临时切换。
