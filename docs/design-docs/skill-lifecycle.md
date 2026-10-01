# Skill 生命周期与一致性

状态：已实现（2026-09-29）。本设计替代 [原资源管理设计](user-skills-and-mcp.md) 中的 Skill 状态、内存草稿和缓存策略；MCP 范围与凭据规则保持不变。

## 背景与目标

同名共享与私有 Skill 原来在管理页按 name 合并正文，遮蔽判断又与运行时不同。管理员「添加」隐式改变全员状态；内容更新只能删除重装；内存草稿不能跨 worker 使用；数据库与文件操作在进程中断时可能只完成一半。

目标是让管理页、Picker 和 Agent 对同一份技能达成一致，支持查看、更新、诊断及恢复，并保留清晰的权限边界。

## 身份与有效状态

数据库 UUID 是资源身份，更新保持 UUID。管理 API 保留显式 `(name, scope)`，服务层从数据库用户解析私有归属；选择器使用 `global:name` / `user:name` 标识，消息执行不能仅凭名称切换到另一范围。内容索引和前端卡片分别用 `(scope, name)` 与 UUID，避免同名覆盖。

`skill_state.evaluate_skills` 是状态计算入口：

- `availability`：ready / missing / invalid / pending。诊断保留具体问题，不回显服务器路径或原始 YAML 异常片段。
- `personally_enabled`：共享项读个人偏好（无行默认启用），私有项读自己的 enabled。
- `effective_enabled`：文件正常、操作完成、全员开放、个人启用且未被有效私有项遮蔽。
- `unavailable_reason`：缺失、异常、待恢复、全员停用、个人停用或同名遮蔽。

只有文件正常且启用的私有 Skill 才遮蔽共享项。管理页、Picker、显式选择和 Agent 构建消费相同结果。

## 操作与数据模型

`skills` 增加 `status`、`content_hash`、`source_url`、`source_ref`；继续使用 version 与 skill_user_states。没有历史字段回退或迁移；开发期表结构变化时按 README 清空并重建整个数据库。

- 安装范围由数据库角色决定：admin/owner 安装共享，member 安装私有；新安装默认停用。
- 「添加到我的技能 / 我不使用」始终是个人操作，管理员也一样。
- 「全员启用 / 全员停用」是独立的管理员操作。
- 更新通过 prepare 的 `target_id` 指定已有资源。每次 prepare/confirm 都重新检查角色和归属；管理员不能修改别人的私有 Skill。包内 name 必须不变。
- 更新保留 ID、归属、全员状态及个人偏好，version 加一；记录完整内容的 SHA-256、来源地址和远程 commit。
- confirm 校验预览时版本和实际文件摘要，同时校验暂存包未被改动。两份并发预览只能先提交的一份成功，另一份要求重新预览。

## 内容预览和依赖

导入与详情返回正文（最多 64,000 字符）、有界文件清单及 SHA-256。更新还返回新增/修改/删除的路径和有界 SKILL.md 文本差异。前端按纯文本展示，不执行 HTML、脚本或包内资源。

可选 frontmatter 示例：

```yaml
melonclaw_requirements:
  commands: [python]
  mcp: [research]
  config: [RESEARCH_API_KEY]
```

每类至多 32 个安全名称。commands 只检查 PATH，mcp 只检查当前用户可见且启用的配置（不代表连接成功）；config 显示「需自行确认」，不会读取应用环境变量或泄露密钥是否存在。缺少依赖不自动安装、不阻止安装、不扩大工具权限。

远程来源先通过 GitHub commits API 把分支/tag/commit 解析为完整 SHA，再下载固定 SHA 的 codeload ZIP。纯仓库默认 main，tree 链接尊重指定 ref；名称含 `/` 的分支暂不支持。远程子目录筛选在读取 ZIP entry 前先校验文件数量、总大小、单文件大小和压缩比，之后复用上传校验。

## 文件布局与可恢复提交

```text
<data_root>/skills/
  shared/<name>/             # 正文唯一编辑入口（共享）
  users/<user_id>/<name>/    # 正文唯一编辑入口（私有）
  tmp/<draft_id>/            # draft.json + extracted，TTL 15 分钟
  .operations/<operation>/  # operation.json + new / old，待恢复文件操作
  .operations.lock          # 同一数据根跨进程互斥
  .snapshots/<digest>/      # global/user 内容快照，仅运行时派生缓存
```

草稿清理依据磁盘 manifest 的 expires_at，不能依据当前进程的内存表；未写完 manifest 的临时目录按目录时间加 TTL 清理。启动另一个 worker 不会删除其他 worker 的有效草稿。

安装/更新：校验权限与版本 → 复制新文件到操作目录 → 原子发布操作日志 → DB 标记 installing/updating → 旧目录移到 old → 新目录移到目标 → DB 提交 ready、version、hash → 清理日志。

删除：发布操作日志 → 将正文目录移到扫描根之外的 old → 删除数据库行 → 清理日志。残留 old 不会被索引重建扫描。

恢复在持锁状态执行：数据库已经提交新版本就保留新内容并清理；未提交就恢复备份和旧元数据；未提交的新安装删除 pending 行及文件。删除如果 DB 行还在就恢复目录，行已删除就清理隔离文件。`db-init`、后续导入/删除和管理员「恢复与检查」会先恢复遗留操作。恢复幂等；文件损坏或外部修改造成冲突时保留日志，不能静默忽略。

索引重建仍然只补缺，不改已有启停或偏好，也不自动删除 missing 行；额外报告 invalid 以及找不到归属用户的目录。恢复与检查仅管理员可执行。

## Agent 数据流

`skill_snapshot` 在同一数据根锁内查询索引、计算有效状态，将有效内容复制到摘要命名的只读路由目录；源文件更新不会改变已构建 Agent 的文件。快照不参与索引重建，所有文件工具写入仍由原有 `FilesystemPermission` 拒绝。

缓存键使用有效目录的资源 ID、版本和完整内容摘要，个人开关、删除或手工改动正文都会改变目录摘要。消息的 display_metadata 保存选中 Skill 引用和本轮可见 `skill_catalog`；它表示可用目录，不虚报为所有技能均已实际调用。实际文件读取可在工具轨迹中观察，并结合记录的内容摘要追踪版本。

上游 SkillsMiddleware 会复用 Checkpoint 的 skills_metadata，单纯重建 Agent 不会刷新。`SkillRefreshMiddleware` 每轮从构建时快照重读摘要，框架原有中间件继续负责 prompt 注入。完整 Agent 图测试覆盖同一会话首轮、更新后下一轮，以及空目录清除旧摘要。

## API 和运行步骤

- `GET /api/skills`：返回可选择的范围化 ID。
- `GET /api/skills/manage`：数据库 ID、selection_id、状态、诊断、来源、版本。
- `GET /api/skills/{name}/details?user_id=...&scope=...`：可见内容的正文/文件/依赖预览。
- ZIP prepare 表单与远程 prepare JSON 可传 `target_id` UUID；确认/取消按 draft_id。
- `POST /api/skills/recover?user_id=...`：管理员恢复日志并重建索引，返回摘要及异常清单。

按 [README](../../README.md) 停服重建 Skill 两表，再执行 `uv run melonclaw-db-init`。上传 → 预览 → 确认 → 启用 → 使用；内容更新通过目标卡片菜单进入，预期 ID 与个人偏好保持不变。损坏或缺失 Skill 仍可删除，缺失正文也可通过更新修复。

## 失败与安全边界

- 本次没有新增 Agent 工具，未扩大 PTC、Shell 或 HITL 审批清单。内容导入/更新/删除是用户主动触发的 Web 管理操作，由服务层范围权限、ZIP 校验、文件锁与日志约束。
- 身份仍是开发模拟 user_id，不能描述成生产认证；LocalShellBackend 仍非安全沙箱。文件快照不是 Shell 隔离手段。
- 文件锁与同盘 rename 要求 POSIX 文件系统语义；不能让多个部署各有一份数据根却共享同一数据库。
- 保障进程中断恢复，不承诺断电、文件系统损坏或部署者手工修改操作日志后的自动恢复。
- 快照目前不自动回收，避免删除运行中 Agent 的文件；停服且无待恢复执行后可清理派生缓存。审批/用户问题恢复仍沿用现有 Agent 重建链路，不承诺跨重启固定整个 Agent 工具与模型环境。
- 没有自动更新、完整历史版本管理、病毒扫描或自动依赖安装。依赖提示不是安全审计结论。

## 验证记录

- `tests/test_skill_lifecycle.py`：跨实例草稿、更新身份和来源、版本竞争、权限复核、提交前后中断恢复、删除不复活、声明依赖、快照不可变与同名隔离。
- `tests/test_skill_refresh.py`：本地假模型 + 真实 Deep Agent 图与 Checkpoint，验证每轮摘要刷新。
- `tests/test_skill_remote.py`：ref 固定及子目录 ZIP 超限提前拒绝；无真实外部 GitHub 下载依赖。
- `frontend/tests/resource-skills.test.tsx`：全员/个人动作隔离、同名卡片、更新预览及确认、按 scope 读取正文。
- 真实 PostgreSQL 隔离 schema：建新表、安装、更新、保留个人偏好、可见性、详情及删除通过；临时 schema 已清理，未改当前业务数据。
- `scripts/check.sh` 全量检查通过。
- 本地开发库已备份并重建 Skill 两表，db-init 重新索引 20 个 Skill；模型和会话数据保留。真实 HTTP 安装→个人/全员开关→更新→详情→删除→恢复验证通过，测试 Skill 已清理。
- 页面检查发现第三方 requirements 元数据冲突，应用声明使用独立的 melonclaw_requirements；回归测试覆盖第三方字段不影响可用性。
- 浏览器交互验证共享卡片与详情正文/文件清单；截图工具超时，未完成截图视觉验收。
