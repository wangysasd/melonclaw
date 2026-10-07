# 用户 Skill 与 MCP 资源管理

> MCP 部分已由 [MCP 两层配置与 JSON 导入](mcp-two-layer.md) 更新（2026-10-01）。以下旧 MCP 接口与状态描述仅作历史记录。

- 状态：已实现
- 背景与目标：原先 Skill 固定从仓库根目录 `skills/` 读取、MCP 固定从根目录 `mcp.json` 在进程启动时一次性加载，两者都是部署级静态配置，没有用户级概念。本设计让管理员安装全员共享的 Skill、管理全局 MCP，同时每个用户可以上传自己的私有 Skill、配置自己的私有 MCP。
- 参考：隔壁 Yuxi 项目的"元数据索引在 DB、内容在文件系统"、两段式安装草稿、内置代码→DB 单向同步等设计（详见调研记录）；注入机制按 melonclaw 自身架构（DeepAgents `create_deep_agent` + `CompositeBackend` 每 Agent 构建）重新设计，未采用 Yuxi 的每用户投影目录与工具门控。

> Skill 生命周期已于 2026-09-29 扩展，当前状态、身份、内容更新、草稿持久化、文件操作恢复与运行时快照以 [Skill 生命周期](skill-lifecycle.md) 为准。本文下方保留最初方案与阶段决策记录，其中“内存草稿”“全局 max(updated_at)”“添加即全员启用”已被新方案替代。MCP 设计仍适用。

## 方案概览

### Scope 模型（两级起步）

- `global`：管理员安装 Skill 或创建 MCP，全员可见可启用；
- `user`：仅创建者可见可管理；
- Skill 仅支持 `global/user`，不预留未实现的租户范围。MCP 的范围约束不在此次清理中调整。
- 不做"按指定用户/租户自由分享"：当前 one-tenant-per-user 模型下那是过度设计。

### 数据模型

- `skills` 表：name（共享名称唯一，私有名称按创建者唯一）、scope、source_type（builtin/upload/remote）、enabled（管理员全员开关）、storage_path（相对 `data_root/skills`，不存宿主机绝对路径）、version、created_by。
- `skill_user_states` 表：`(user_id, skill_id)` 主键的个人启停偏好，只用于共享 Skill——默认启用（无行即启用），用户写下 `enabled=false` 表示"我自己不用"，不影响其他用户；私有 Skill 的启停不进这张表，由 `skills.enabled` 表达。
- `mcp_servers` 表：slug、display_name、scope、owner_user_id、created_by、transport、url/command/args/env/headers、tool_allowlist、enabled、version；部分唯一索引分别约束全局 slug 与个人 owner+slug；CHECK 约束限制 scope 归属和连接参数。现行字段以 [MCP 两层设计](mcp-two-layer.md) 为准。
- 文件布局：`<data_root>/skills/shared/<name>/`（global）、`users/<user_id>/<name>/`（user）、`tmp/<draft_id>/`（上传草稿）。
- **文件系统是 SKILL.md 的唯一事实来源**，DB 行只做"存在 + enabled + scope + 个人偏好"索引；列表 = 磁盘扫描 ∩ DB 可见行，避免正文双写。

> 索引自愈（2026-09）：索引既然是投影而不是事实，就必须能从事实重建。`melonclaw-db-init` 在建表/种子之后调用 `services/skill_index.py` 的 `reindex_skills_from_disk()`，扫 `shared/<name>` 与 `users/<uid>/<name>`，对磁盘上有、DB 里没有的 Skill 补一行；**只补缺、不覆盖**，已有行（管理员改过的 enabled、安装时确定的 scope）原样保留，因此可反复重跑。没有这一步时，清空数据库会让 20 个共享 Skill 目录在接口上"消失"——目录还在、索引空了，Picker 与执行白名单同时变空，且不报任何错。用户目录若无对应用户行则记入 `orphaned` 并打 warning（`created_by` 有外键，硬插会失败）。
>
> **反方向（DB 有行、磁盘无目录）有意不删，不是遗漏。** 两个理由：一是 `data_root` 配错或挂载未就绪时，"扫不到"会被当成"不存在"，一次 db-init 就把索引全清空，配置错误升级成数据事故；二是 `SkillCatalog` 会跳过 frontmatter 坏掉、name 与目录名不一致、超限的目录，这些目录其实还在磁盘上，删行会连带级联清掉 `skill_user_states` 里全员的个人偏好——有人改 SKILL.md 打错缩进，管理员就得重配一遍。孤儿行不会静默：`reindex_skills_from_disk()` 会按行上的 `storage_path` 核对目录是否存在，不存在就记入 `missing`、打 warning 并在摘要里列出（db-init 每次都报，直到处理掉）；`manageable_skills()` 又是按行返回而非取磁盘交集，所以它在资源管理界面照样列出，用 `DELETE /api/skills/{name}` 手动删掉即可（`delete_skill` 用 `rmtree(..., ignore_errors=True)`，不要求目录存在）。判据用行上的 `storage_path` 而不是"扫描结果里有没有"：目录还在、只是 SKILL.md 坏了的话，扫描会跳过它，按扫描结果判就会把一行好数据误删。
>
> 由此得到一条可复用的判据：**文件系统决定"存在"，数据库决定"存在的东西当前是什么运营状态"。** 重建只决定行的有无，绝不改写已有行的 `enabled`/`scope`/`storage_path`。
>
> 界面上的对应物（2026-09）：`manageable_skills()` 每项都带 `availability`（`ready`/`missing`/`invalid`），管理页据此给"行还在、磁盘上读不出来"的行加红徽章和一句"该做什么"，并禁掉添加与使用——这类行不进 Picker，不标出来用户无从发现，标了又不禁用则点下去必然失败。`missing`（目录没了 → 只能删行重传）与 `invalid`（目录还在、SKILL.md 读不出来 → 修好文件即自动恢复）**必须分开**：合成一个布尔会逼前端一律说成"目录已丢失"，把用户引去删一个其实还在的技能。注意 `invalid` 也覆盖目录名不合规（`SAFE_DIRECTORY_RE` 不匹配）等情况，所以文案写"技能文件异常"而不是"SKILL.md 异常"。删除入口始终可用——那是用户唯一的自救入口。

> 管理卡片交互（2026-09）：卡片不显示 Skill Logo，标题下展示由资源记录推导的来源；右上角主按钮按当前用户状态显示「添加」或「使用」。添加按 scope 调用个人启停或全员启停接口，使用会新建空白对话并预选 Skill。右上角「…」菜单提供「下载」：管理员对启用的共享项显示「全员停用」，普通用户对共享项显示「我不使用」（仅修改个人偏好），私有 Skill 显示「卸载」。全员停用的共享项只在管理员管理页保留，可再次「添加」或删除；普通用户管理页和所有 Picker 均不展示。菜单权限仍由服务端校验。当前没有可靠的使用人数数据，因此不显示截图中的人数统计。搜索框缩到原宽度约一半；列表不再使用内部固定高度，长列表沿资源页继续向下滚动。

### 内置 MCP 种子

仓库 `mcp.json` 保留为种子：`melonclaw-db-init` 时单向同步进 DB；新导入的系统 MCP 默认全员启用，用户没有个人停用偏好时显示“已添加”。已存在行不覆盖管理员改过的运营字段。运行时切断文件直连，唯一入口是 DB。Tushare 工具白名单环境变量在种子阶段落进 `tool_allowlist` 列。

> 2026-10 更新：运行时 Skill 唯一存储是 `MELONCLAW_DATA_DIR/skills/`，默认根为 `~/.melonclaw/data/`。仓库 `.data/skills/shared/` 仅分发内置模板，首次安装复制，升级不覆盖运行时内容；用户新增/更新走资源管理 UI 或 skill_import 服务。`source_type='builtin'` 表示 db-init 从共享目录登记的 Skill。

### Skill 注入

双虚拟路由：`/skills/`（共享根）与 `/skills-user/`（当前用户私有根），每 Agent 构建时按用户挂载（`CompositeBackend` 本就每请求新建）。可见性公式：共享行 = 全员开关（`skills.enabled`）开着 ∧ 个人偏好（`skill_user_states.enabled`，无行即启用）未关；私有行 = 创建者是自己 ∧ 行级 enabled。Agent 缓存键追加 `max(updated_at)` 版本戳做配置变更失效——用全局最大时间戳换取实现简单与多进程一致（代价是任何用户改配置会让所有缓存键变一次，多重建一个 Agent）。个人偏好变化不进缓存键：它只影响 Picker/白名单的可见行，每次查询现算。

### MCP 注入与安全边界

- 运行时装配唯一入口 `services/mcp.py`：查可见行 → `row_to_client_config()` → 按 scope 展开 `${VAR}`。
- `global` 行允许 stdio 和 `${VAR}`（展开用进程环境），发布需管理员权限；
- `user` 行仅 http/sse，保存时校验 `${` 直接 400，装载时用空环境二次兜底——机制上阻断用户 MCP 引用应用自身密钥。

### 上传与安装

- ZIP 两段式：`prepare`（multipart 上传 → 静态校验 → 临时目录 + 内存草稿，TTL 15 分钟）→ 前端预览 → `confirm`（原子移动 + 落库，失败整体回滚）。校验含 zip 完整性、压缩比 ≤100:1、总量 ≤50MB、单文件 ≤10MB、逐 entry 路径规范化（拒绝绝对路径/`..`/反斜杠穿越，手动写盘不调 extractall）、frontmatter 复用 SkillCatalog 规则、name 全局唯一。
- 远程市场：GitHub zipball 纯 HTTP 下载（host 白名单 `codeload.github.com` 等），下载后走与 ZIP 完全相同的校验与确认，**零代码执行**。skills.sh CLI 安装需要执行不可信代码，本设计不提供（Yuxi 用一次性沙箱，melonclaw 当前无沙箱能力）。

### API 与权限

- `GET /api/skills`（可见且启用）、`GET /api/skills/manage`（全员停用的共享项仅返回给 admin/owner；私有停用项仍返回；共享项附带 `user_enabled` 个人偏好）；导入 prepare/confirm/cancel、远程 install、PATCH/DELETE（个人启停与删除）、PATCH `/{name}/global-state`（全员启停共享项，仅 admin/owner）。
- `GET/POST /api/mcp`、`PATCH/DELETE /api/mcp/{slug}`。
- 权限矩阵：member 管理自己的 user scope；admin/owner 安装 Skill 时直接创建 global，并可管理 global 资源；**任何人（含 admin）不能修改他人创建的 user scope 资源**。判定复用 `users.tenant_role`。共享 Skill 的个人启停对所有人开放（"用户可以不使用"，只影响自己）；全员启停仅 admin/owner。

## 关键取舍

1. **一张 `skills` 表 + scope 枚举**，不拆 shared/user 两张表：元数据/解析/合并逻辑只写一份，安装时根据数据库中的当前角色确定 scope 和存储目录。
2. **不做每用户投影目录**（对比 Yuxi）：单进程 asyncio + 两级 scope 下投影是纯开销；权限变化通过缓存版本戳即刻生效。
3. **`${VAR}` 按 scope 区分而不是按操作者角色**：规则只认 scope 不认人，避免"管理员自己的私有 MCP 是否受限"这类特殊分支。
4. **Agent 缓存版本戳用全局 max(updated_at)**：不做 per-user 代际计数器，接受偶发多余重建换取多进程一致。
5. **曹操（默认模拟用户）提升为 admin**：权限矩阵需要至少一个管理员；种子 upsert 现在会同步 `tenant_role`。

## 失败与安全边界

- 恶意 SKILL.md 提示注入：global 由 admin/owner 安装前确认来源可信；user skill 只能污染创建者自己的会话。SKILL.md 保留 10MB 上限。
- zip 炸弹/路径穿越：全部静态校验在 prepare 阶段，confirm 不再碰 zip；手动写盘使符号链接 entry 无害化（只写普通文件，不创建链接）。
- 凭据泄漏：user MCP 禁 `${VAR}`；`GET /api/mcp` 只回显 env/headers 键名不回显值；错误日志沿用 `redact_mcp_sensitive_text`。global MCP 可引用部署密钥是明确的产品决策，靠"发布需 admin"兜住。
- 用户级 MCP 拖慢 Agent 构建：每 server 失败不阻断（现有 gather 兜底）；配置不变时聊天零重建。
- 已知限制：内存导入草稿进程重启即废（TTL 15 分钟，可接受）；多进程部署下缓存版本戳以启动时 DB 时间戳为准，配置变更与缓存失效之间存在小窗口。

## 验证方式

- 单元测试：`tests/test_skill_import.py`（校验与状态机）、`tests/test_mcp_service.py`（装配与 `${VAR}` 边界）、`tests/test_schema.py`（新表约束）、`tests/test_skills.py`（多根 catalog）。
- `tests/test_architecture.py` 强制依赖方向；docs/ARCHITECTURE.md 的横切入口表已同步更新。
- 端到端：`melonclaw-db-init` 幂等重跑后现有 Skill/MCP 入 DB 且 Agent 行为不变；上传 ZIP → 确认 → Picker 立即可见；用户 A 看不到用户 B 的私有 Skill；admin/owner 安装直接全局可用，member 安装仅自己可见；用户 MCP 配 stdio 或 `${VAR}` 被 400。

## 安装范围简化（2026-09-28）

上传与远程安装共用 `SkillImportService.confirm`：确认时查询数据库用户上下文，admin/owner 写 `global` 与 `shared/<name>`，member 写 `user` 与 `users/<user_id>/<name>`，安装后停用，点击「添加」启用。客户端不能指定范围；没有独立发布按钮或接口。两段式预览仍用于确认安装内容，不是发布审核。

共享 Skill 默认对全员可用，各用户可以个人停用；普通用户不能共享私有技能。共享名称唯一、私有名称按创建者唯一；私有安装不得与已启用的共享项重名。保留 ZIP 校验、草稿归属与有效期校验、失败清理；没有新增 Agent 工具或放宽 Shell/HITL 边界。用户身份仍为开发模拟身份。旧个人记录不自动转为共享，需要删除后重新安装；无需修改表结构。

验证：`scripts/check.sh`；安装测试覆盖 upload/remote × admin/owner/member、无效用户和共享安装失败回滚。安装后检查管理页范围分组、数据库 scope/storage_path/enabled 与文件目录。

## 开发期数据重置约定

Skill 只读取当前表结构与 `shared/`、`users/` 文件布局，不迁移旧目录，不为缺失的数据库字段填默认值。旧结构直接清空重建，再运行 `uv run melonclaw-db-init`；旧用户的私有文件应同步清理并重新安装。共享正文可保留，由 db-init 登记索引。个人偏好值为 `None` 是当前模型的“默认启用”语义；索引重建、文件缺失/损坏提示和上传 ZIP 布局归一化仍是正常功能。

管理 API 的 scope 为必填值，下载、个人启停与删除统一显式选择 global/user；移除按省略范围推断记录的分支。旧 Skill 表约束按 README 的重建步骤处理，不提供迁移。
