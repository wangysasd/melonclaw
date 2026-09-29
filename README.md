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
- 管理 Skill：打开侧栏「插件」→「Skills」，可搜索、上传或远程安装。安装后默认停用；私有 Skill 点击「添加到我的技能」，共享 Skill 由管理员从「…」菜单「全员启用」后开放。管理员的「我不使用」也只影响自己，「全员停用」才影响所有用户。「使用」会创建已选中技能的新对话。
  - 点击卡片查看正文、文件清单、来源、版本和依赖提示；文件损坏或缺失会显示诊断，可通过「上传更新内容」修复，或删除资源。
  - 更新：在目标卡片「…」菜单选择「上传更新内容」或「从远程更新内容」，查看正文差异和文件增删后确认。包内 `name` 必须保持不变；更新保留资源 ID、归属、启停和个人偏好。如果预览后其他人更新了内容，需重新预览。
  - 远程地址支持 `owner/repo`、`https://github.com/owner/repo`（默认 main）和 `https://github.com/owner/repo/tree/<分支或commit>/<子目录>`。服务先通过 GitHub API 解析 commit 再下载固定版本；不执行远程安装脚本。当前不支持名称含 `/` 的分支。GitHub API 限流或无法解析版本时会明确失败，可改为上传 ZIP。
  - 上传预览显示范围、正文、文件清单、来源和依赖。草稿有效期 15 分钟，重启后可在有效期内继续确认；多个 Web worker 必须共享同一数据根和支持 POSIX 文件锁的文件系统。远程下载阶段可以取消，确认已提交后不能撤销。
  - `SKILL.md` 可选声明 `melonclaw_requirements.commands`、`melonclaw_requirements.mcp`、`melonclaw_requirements.config` 名称列表。检查只探测命令是否存在和当前用户启用的 MCP 是否已配置；配置项需自行确认，不读取或展示服务器密钥。缺少依赖仅提示，不授予权限或自动安装。
  - 同名时只有有效启用、文件正常的私有 Skill 才遮蔽共享项；停用或损坏私有项后恢复共享项。管理页、Picker、显式选择与 Agent 自动发现共用同一有效状态；消息请求的 `skill_id` 使用目录返回的范围化 ID（如 `global:report`），不能自行省略范围。
  - 管理员可用「恢复与检查」处理未完成操作并重建索引。它不会删除缺失文件的记录；`missing`、`invalid` 和孤立用户目录需要按报告处理。正文与数据库之间的写入由操作日志恢复；删除先隔离目录，避免索引重建重新登记已删除技能。
  - 每轮消息记录选中技能和可用目录的资源 ID、版本、内容摘要；Agent 使用只读内容快照，更新从后续构建生效。快照属于可再生成的运行缓存，不是正文的编辑入口；不要在有运行或暂停的会话时清理。

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

- 主助手提示「模型工具调用协议异常：工具名称为空」→ 本轮已终止，该批工具未执行，不会自动重试；可切换模型后重试，或由管理员排查供应商接口与流式解析。此前已完成的工具操作不会回滚。

- 端口被占用 → `scripts/shutdown.sh` 后再启动
- 启动提示表结构不一致 → 备份后清空数据库，重跑 `uv run melonclaw-db-init`
- 模型下拉为空 → 确认已运行 `uv run melonclaw-db-init`，并在资源管理界面检查模型是否已配置 API Key
- 会话显示「上次请求尚未结束」且重新同步无效 → 上一轮因服务重启等原因中断；服务重启时会自动把这类遗留轮次标记为失败，重新同步会话或刷新后直接发新消息即可继续

边界：

- `.env` 只放本地，不提交。
- 页面用户是开发模拟身份，不是生产认证；同租户用户也不能访问彼此的项目、会话或附件。
- `LocalShellBackend` 不是沙箱，仅限本机 `127.0.0.1` 单用户开发使用。

### 供应商高级配置

在插件 → 模型页点击卡片，管理员可通过双列表单编辑供应商；点击「确定」保存配置，供应商启用状态按表单中的状态开关保存。高级配置默认收起，点击展开后支持请求头 JSON（只写不回显，留空保留，`{}` 清空）与供应商扩展请求体 JSON（不可覆盖模型、消息、工具、流式协议或写入凭据）。API Key Env 填大写且以 `_API_KEY` / `_ACCESS_TOKEN` 结尾的变量名；全局模型在个人 Key、数据库共享 Key 均未设置时才使用该环境变量。个人模型仍只使用个人 Key。修改环境变量后重启服务。Provider Type 当前只支持 OpenAI Completions API。

供应商仅支持 `global`，模型仅支持 `global/user`。恢复环境变量凭据功能本身不需要重建已有 `api_key_env` 列的数据库。若要让旧 scope 约束与当前定义一致，按开发期约定删除并重建模型三表（仅清空行无效），然后执行 `uv run melonclaw-db-init`，再运行 `scripts/restart.sh`；该操作会重置模型配置和个人 Key。详见 [供应商设计](docs/design-docs/custom-models.md)。


普通用户配置个人 Key 后可独立添加和使用个人模型，不受供应商全局 enabled 开关影响；管理员开关仅控制内置模型。个人模型仅使用个人 Key，清除后不可用，不借用共享 Key。连接地址仍由管理员维护。

管理员停用供应商并保存时，会清除数据库共享 Key 和 API Key Env 引用；部署环境变量与各用户的个人 Key 保留。重新启用需重新配置共享凭据。

默认模型按归属独立保存：全局一个、每个用户一个个人默认。可用的个人默认优先于管理员内置默认；个人默认不可用时使用内置默认，两者都不可用时优先首个可用内置模型，再选个人模型。配置刷新时聊天选择同步服务端默认，用户仍可在聊天中临时切换。

上传的 ZIP 应在根目录直接包含 `SKILL.md`，或只包一层 Skill 目录；每次上传一个 Skill。支持 macOS 原生压缩包，自动忽略 `__MACOSX`、`.DS_Store` 和 `._*` 元数据。

管理员（admin/owner）上传或远程安装 Skill 后存为共享项，点击「添加」后全员可用；普通用户安装后点击「添加」，仅自己可用。共享 Skill 可由各用户单独卸载停用。安装范围由服务端读取数据库角色决定。

Skill 管理 API 的个人启停、下载与删除必须传 `scope=global` 或 `scope=user`，不再省略范围自动推断。Skill 仅支持这两种范围。当前 Skill 表新增 `status/content_hash/source_url/source_ref`。旧数据库需删除并重建 `skill_user_states`、`skills` 表（仅清空行不会更新字段或约束），再执行 `uv run melonclaw-db-init`；这会重置 Skill 索引与个人启停偏好。旧私有文件按需清理后重新安装，共享正文可保留用于初始化登记。

停止服务后，可仅重建 Skill 两表（清除 Skill 索引与个人偏好，保留正文、会话和模型配置）：

```bash
uv run python - <<'PY'
import asyncio
from melonclaw.core.config import load_settings
from melonclaw.database import Database
from melonclaw.database.schema import skills, skill_user_states

async def reset_skills():
    database = Database(load_settings().database_url)
    await database.open()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(lambda sync: skill_user_states.drop(sync, checkfirst=True))
            await connection.run_sync(lambda sync: skills.drop(sync, checkfirst=True))
    finally:
        await database.close()

asyncio.run(reset_skills())
PY
uv run melonclaw-db-init
scripts/start.sh
```

新增 Skill 接口：`GET /api/skills/{name}/details?user_id=...&scope=global|user` 返回有界正文/文件预览；`POST /api/skills/recover?user_id=...` 仅管理员恢复操作并返回索引诊断。ZIP `import/prepare` 表单与远程 `install/remote` JSON 可带 `target_id`（管理接口返回的数据库 UUID）进入更新流程；确认、取消仍按 `draft_id` 操作。


## 聊天安装 Skill 与内置教程

可以直接问“怎么配置 Skill / MCP / 模型”，AI 会按需读取内置 `melonclaw-tutorial`，给出页面入口、操作步骤和排障方法。它由 db-init 从共享目录补登记，新增时默认启用；已有停用或个人偏好不被覆盖。没有可用模型时先从聊天模型选择器的“添加自定义模型”进入“拓展 → 模型”配置，静态提示不依赖 AI。

聊天安装支持两种方式：

- 上传 Skill ZIP 附件，发送“帮我安装并启用这个 Skill”。
- 发送公开 GitHub 仓库或 Skill 子目录链接，并说明“帮我安装”。集合仓库需指定单个 Skill 子目录；纯仓库默认 main。

AI 准备预览后显示一次审批：确认名称、来源、安装范围和是否启用，选择允许并提交。默认安装并启用，想暂不启用可说明“只安装”。管理员／owner 安装为共享资源，启用会对全员开放；普通用户仅自己可用。安装并启用后，下一条消息可自动发现或从技能选择器选择。拒绝审批不会安装；安装不会执行包内脚本或自动装依赖。同名更新请使用“拓展 → Skills”的卡片菜单。

ZIP 不作为文档解析，不要求视觉模型；聊天上传默认单文件 20MB，仍受附件弹窗显示的数量、总大小和压缩比限制及 Skill 导入校验。草稿 15 分钟过期后重新准备。若中断后不确定是否安装成功，先到 Skills 管理页核实，管理员可用“恢复与检查”。当前开发模拟身份和 LocalShellBackend 的部署限制不变。

**已有开发库需要重建附件表的 CHECK 约束。** 本次增加 archive 附件类型，仅运行 db-init 不会修改旧约束。先停服、备份需要保留的数据，在数据库管理客户端对目标开发库执行以下 SQL（会清空附件及消息附件绑定，不清空模型和聊天文本）：

```sql
DROP TABLE chat_message_attachments;
DROP TABLE chat_attachments;
```

随后在项目根目录执行：

```bash
uv run melonclaw-db-init
scripts/restart.sh
```

新数据库直接运行初始化即可。旧附件文件不会被上述 SQL 自动删除，应在停服时按保留需求单独处理；旧聊天附件不再可用。旧 Skill 暂存草稿也应在更新前过期或清理，用户重新准备即可。无需安装新的 Python 或前端依赖。

如果使用自定义数据根，初始化前将仓库的教程目录部署到该数据根（不要把数据根本身设为教程目录）：

```bash
mkdir -p "${MELONCLAW_DATA_DIR:?请先设置自定义数据根}/skills/shared"
cp -R .data/skills/shared/melonclaw-tutorial "${MELONCLAW_DATA_DIR:?请先设置自定义数据根}/skills/shared/"
uv run melonclaw-db-init
```

上述复制命令要求先设置 `MELONCLAW_DATA_DIR`；默认 `.data/` 无需复制。实现及验证见 [聊天安装设计](docs/design-docs/chat-skill-install.md)。
