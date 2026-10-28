# 内置教程与聊天安装 Skill

状态：已实现
日期：2026-09-29

## 背景与目标

用户希望在聊天中学习 MelonClaw 的操作，也希望通过 ZIP 附件、GitHub 或公网 HTTPS 下载链接完成 Skill 安装。教程提供知识，系统工具提供执行能力，二者互不依赖。

## 方案与实现位置

- `.data/skills/shared/melonclaw-tutorial/`：入口及 Skill、MCP、模型、排障参考。通过既有 `skill_index` 初始化，内置新增行默认启用，不覆盖已有开关和个人偏好。自定义数据根需部署这份目录到相应 shared 目录。
- `tool/skill_install.py`：两个工具及 provider 协议；`ToolRuntime[Any]` 隐式注入上下文，模型 schema 无 user_id、tenant_id、conversation_id 或本地路径。
- `services/skill_install.py`：聊天适配器校验用户租户、会话与项目归属，附件只接受服务端映射的 ID；调用 `ChatService` 已有 prepare 门面及 `SkillImportService.confirm`，不回环请求 HTTP、不直接写 SQL。
- `core/agent.py`、`services/runtime.py`、`services/execution.py`：注入 provider 和当前 conversation_id；两个工具常驻主模型目录，不受 MCP 动态筛选遗漏影响。未加入 PTC。
- `parsers/archives.py`、附件服务/仓储/模型 hydration：ZIP 属于 archive，校验容器、路径、链接、数量、大小和压缩比；不解压、不生成 Markdown、不调用文档解析器。模型收到文件名与 attachment_id。
- `core/hitl.py`、前端 `SkillInstallApproval`：一次 approve/reject 审批，显示来源、安装范围和启用影响，原始摘要和 commit 可展开查看。

## 数据与事件流

1. ZIP 上传到现有受控附件存储；发送消息后绑定附件。GitHub 链接随用户文本提交。
2. `prepare_skill_install(github_url | attachment_id, enable=true)` 从运行上下文取身份，服务端复核用户、租户、会话和项目。附件要求已发送，校验归属、类型、字节数与上传 SHA-256。
3. 复用既有 prepare：下载远程来源前固定 commit；生成持久草稿、正文/文件/依赖预览。草稿增加 conversation_id 和 enable_on_install，页面草稿 conversation_id 为空。
4. 返回 installation：draft_id、name、scope、source_url、source_ref、content_hash、enable、operation。模型展示预览并原样调用 `confirm_skill_install`，HITL 暂停。
5. 用户允许后再次解析数据库身份与会话归属，确认草稿属于该用户和该会话；逐字段对比审批清单。页面确认接口不能提交聊天草稿，聊天工具不能提交页面草稿。
6. 原 confirm 继续校验角色、同名冲突、草稿 TTL 和暂存内容摘要，然后使用原文件操作日志安装。新安装的 enabled 状态在 ready 的数据库提交中一起写入；更新仍保留原状态。默认 enable=true，用户可在 prepare 时要求只安装。
7. 返回 installed 或明确错误。前端收到安装工具结果后刷新技能选择器；后续消息按新有效状态构建快照，当前运行不热加载。

## 关键取舍

- 安装范围沿用既有规则：admin/owner 为 global，其余为 user。global 的 enable=true 对全员开放；审批明确展示，不静默改为个人资源。
- 教程是普通共享 Skill，可停用，不绕过用户选择。没有可用模型时无法调用教程，模型选择器提供静态配置指引。
- 聊天按当前身份可维护的 scope + SKILL.md name 自动查找更新，保留资源 ID、原创建者和启用状态；相同内容不写入。个人安装仍遵守与已启用共享项的重名约束，不越权更新共享项。集合仓库可指定子目录，ZIP 合集支持有界展开。
- 复用业务附件表与导入服务，当前附件 kind CHECK 包含 archive；草稿结构由持久 manifest 校验。初始化与升级遵守架构约定，不把清理数据库或暂存目录当作正常安装步骤。

## 失败、安全和当前边界

prepare 不进 HITL：副作用限于有大小限制的 HTTP 下载和数据根临时草稿，不修改有效 Skill，不执行内容。confirm 进固定审批清单且不允许 edit，清单改变须重新 prepare。安装工具不能进入 PTC。包正文是不可信数据；静态校验和依赖提示不是安全审计，不执行脚本或自动安装依赖。

原草稿 15 分钟 TTL、内容摘要、文件锁及操作日志恢复继续生效。成功后草稿删除，重复提交拒绝，不重复安装；若进程在提交完成后丢失响应，应到管理页核实，不能承诺自动重试返回成功。模型和浏览器仅报告服务返回结果，数据库或文件失败不回显凭据和宿主机路径。

当前开发模拟 user_id 不是生产认证，运行上下文只是防止模型自行指定身份。LocalShellBackend 不是沙箱，提示词约束不能替代共享部署的可信认证、文件及进程隔离。

GitHub 来源限公开仓库及 tree 子目录；纯仓库默认 main，不支持名称含斜杠的分支或私有仓库凭据。普通下载支持 HTTPS/443 公网接口及查询参数，不要求 ZIP 后缀。逐跳解析 DNS，拒绝非公网/组播/保留地址，固定实际连接 IP 并保留 Host 与 TLS/SNI 原始域名；禁用连接复用避免跨域重定向复用 TLS 身份，不读取进程代理、Cookie 或登录凭据。最多 5 次重定向，单下载总时限 120 秒、大小 50MiB。ZIP 聊天上传受附件上限（默认单文件 20MB）和导入上限（50MB）共同限制，容器压缩比限制也可能比管理页更严格。

## 初始化、操作和预期结果

按 [README](../../README.md#聊天安装-skill-与内置教程) 更新代码并重启服务；本次增强没有数据库/依赖变更。首次建库才执行 `uv run melonclaw-db-init`，既有库升级使用 `uv run melonclaw-db-update`。默认数据根已有教程时会自动补索引；自定义数据根要先复制教程正文。

- 输入“如何配置模型/MCP/Skill”：读取教程中对应参考，返回实际界面步骤，不虚构个人配置。
- 上传合法单 Skill ZIP，发送“帮我安装并启用”：看到预览和一次审批，允许后下一条消息可用。
- 发送公开 GitHub Skill 子目录链接并要求安装：固定 commit 后走同一流程。
- 拒绝审批：没有安装或更新；版本竞争、过期、越权或篡改草稿返回错误并保留旧内容。

## 验证

`tests/test_chat_skill_install.py` 覆盖工具身份隐藏、真实文件导入、清单篡改、用户/会话/角色复核、入口隔离、附件变更、GitHub 下载链路和真实 Deep Agent 图的 approve/reject。现有 Skill 生命周期测试覆盖原导入校验、更新竞争、进程恢复和后续快照刷新。前端测试覆盖 ZIP 能力及共享安装影响展示。

框架身份注入按当前安装版本及 [LangChain 工具文档](https://docs.langchain.com/oss/python/langchain/tools) 核实。最终检查和外部服务验证记录见执行计划。


## 批量同步与合集

主聊天工具增加 `prepare_skill_batch(source_urls, enable)` 和 `confirm_skill_batch(installations)`。最多 32 个地址、4 个并发下载、整批准备预算 240 秒；到期保留已完成项，取消未完成下载并逐项报告超时，逐项复核身份与会话；下载后只按 ZIP 结构展开 `skills/` 路径下的子 ZIP，不遵循包内命令。共用每个来源的 50MiB 展开预算、5000 条目及压缩比限制，最多两层嵌套与 64 个叶子技能；外层合集说明不作为独立技能发布。每个叶子包继续通过单技能完整校验，source_ref 记录成员路径。

全部下载完成后按 scope/name 分组：相同内容保留一项，重复项撤销草稿；同名不同内容整组标记 conflict，不能按下载顺序覆盖。与本地相同的项标记 unchanged 并撤销临时预览，不写有效资源。成功准备项组成安装清单，一次 HITL 确认后逐项调用现有 SkillImportService.confirm，原版本检查、摘要绑定、文件操作日志、恢复和资源版本发布继续生效。各技能独立提交，返回 completed/partial/error 及逐项 installed/updated/unchanged/error；重试只重新准备失败项，丢失回执后可重新准备并通过内容比较核实。

前端复用审批卡展示各项新增/更新、来源、范围和启用状态，原始清单可展开；批量结果到达后刷新技能选择器。新工具常驻目录且不进入 PTC。下载与暂存免逐次审批，正式批量确认在 `core/hitl.py` 登记。包中要求配置定时更新或执行脚本的说明仅是数据，不构成用户授权。

混合安装和环境变量消息先走 `services/environment_chat.py`，值提取后仅保留脱敏正文、链接与草稿引用，再进入模型。变量沿自己的审批、原子提交与幂等链路，不把值作为 Skill 工具参数，也不承诺文件与变量整体原子。详见[环境变量设计](environment-and-preparation.md)。

`tests/test_chat_skill_batch.py` 验证自动更新、原身份和停用状态保留、无变化跳过、嵌套合集去重、冲突、部分失败、版本竞争、身份及真实图审批；`tests/test_skill_remote.py` 验证下载接口、DNS/IP 固定与 TLS 身份、内网重定向拒绝及合集预算。真实外部包的临时下载不代表实际安装或业务接口可用。


### 2026-10-28 外部示例验证

使用新增的公网下载器只读下载用户提供的 20 个 CICC 地址（未使用用户凭据、未写入运行资源）：全部下载成功，19 个来源通过结构/Skill 校验；`cicc-research-analyst-wwd-skill` 的 SKILL.md YAML 不合法，明确失败，不修改上游文件或宽松解析。两个 instruction 地址均为内嵌 7 个 ZIP 的合集。

使用这些原始字节在临时目录复用聊天批量服务与假仓储验证：20 个来源展开 32 项，17 项可提交、9 项相同重复、5 项属于两个同名不同内容冲突组、1 项无效。冲突技能为 `cicc-investment-wiki-answer` 和 `cicc-research-analyst-myl-skill`，必须选择来源；不根据普通/高级链接顺序猜测版本。17 项在临时目录成功提交，未安装进应用或配置真实环境变量。下载响应与内容可能随上游变化，以上为本次观察结果。
