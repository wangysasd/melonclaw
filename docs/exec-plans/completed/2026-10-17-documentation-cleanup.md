# 当前实现文档整理

状态：已完成
开始日期：2026-10-17

## 目标与边界

按用户要求整理 docs 与 note，删除已经退出当前实现的方案，合并重复笔记，修正混合文档中的失效说明。保留当前能力、失败边界、数据库升级与验证证据。此次只有文档变更，不继续实施代码审计的 B/C 类源码候选。

整理开始时 main/HEAD 为 df1587a76c15f49434872aa8e50d1a5a8b936a62；前一轮 A 类清理尚未提交，工作区原有源码差异和审计报告全部保留。整理前另保存 docs/note 的完整本地临时副本，包括 Git 忽略的 note；备份不加入仓库。

## 步骤

- [x] 枚举文档、核对当前模块、路由、注册、前端挂载与现有测试。
- [x] 将仍有效的模型、资源、消息流、身份和附件能力说明合并到现行设计。
- [x] 删除被替代的执行计划与本地研究/需求草案，重建索引并修正引用。
- [x] 校验相对链接、文档源码路径与非文档文件未变化；记录范围和回退方式。

## 删除与承接清单

| 删除文档 | 当前实现证据与承接位置 |
|---|---|
| active/2026-09-26-model-provider-yuxi-parity.md | 初始模型种子、私有供应商与清库方案已被替代；保留[当前模型设计](../../design-docs/custom-models.md)及 model_configs/runtime/model 管理测试 |
| completed/mcp-health-badge.md | McpManager 列表不再自动探测或显示健康胶囊；[MCP 设计](../../design-docs/mcp-two-layer.md)保留主动详情发现、白名单、缓存及取消 |
| completed/one-tenant-per-user.md | 旧演示用户、客户端身份和重建记录已过时；[唯一租户归属](../../design-docs/one-tenant-per-user.md)与[账户设计](../../design-docs/user-management-login.md)承接现行 Cookie/归属规则 |
| completed/2026-10-03-response-fluency.md | 按轮次初选被 ToolPoolMiddleware 取代；[按需工具池](../../design-docs/on-demand-tool-pool.md)与[回复协议](../../design-docs/response-fluency.md)承接现行协议和并行工具结果 |
| completed/2026-10-03-live-response-visibility.md | 旧按轮次预选与单段思考展示已替代；[消息流](../../design-docs/assistant-message-stream.md)保留即时正文、快照对账与隔离边界 |
| completed/2026-10-04-execution-visibility.md | 旧独立思考栏与旧失败记录不再作为当前验收；[执行展示](../../design-docs/agent-execution-display.md)保留当前有序思考/工具条目及折叠规则 |
| note/沙箱原理学习-yuxi-octop.md | 外部项目研究不是本仓库沙箱实现；架构继续明确 LocalShellBackend 非沙箱 |
| note/上传附件技术设计.md | 保留已实现的类型/权限/请求副本边界到[附件设计](../../design-docs/chat-attachments.md)；未实现 OCR、对象存储与过时依赖源码行号不作为现行功能 |
| note/任务中心需求文档.md、task-detail-audit 下三个 Markdown | 旧独立详情与通知形态已退出；[任务设计](../../design-docs/task-center.md)和[当前验收映射](../../design-docs/task-center-acceptance.md)承接固定会话、运行抽屉与三表升级 |
| note/会话项目结构Codex化设计.md | [工作区](../../design-docs/conversations-and-project-workspaces.md)、[侧栏](../../design-docs/sidebar-resource-management.md)和附件设计承接当前已实现边界 |
| note/用户租户管理与登录页-MVP需求设计.md | 需求草案的覆盖式约定已由现行账户、租户和两品牌登录设计取代 |
| note/目录方案.md | [部署](../../deployment.md)、[首次上线](../../first-deployment.md)、[持久文件](../../design-docs/persistent-user-files.md)保留现行目录与模板安装约定 |
| note/聊天交互优化建议.md | 已实现部分在确认卡、结果组件、长任务反馈与错误恢复设计中；未交付建议不作为产品说明保留 |
| note/code-review-20261006.md | 四项账户问题已有[修复设计](../../design-docs/account-review-fixes.md)及回归；两项尚存后台流边界明确写入 D13 和质量记录，保留 strict xfail 测试 |

共删除 6 份执行计划、11 份本地 Markdown；note/note.md 改为当前能力定位表。note/middleware-luna-review.md 仍对应存在的离线中间件测试，保留带日期的验收证据；本地图片未纳入此次 Markdown 删除范围，也不作为当前状态证明。

## 保留文档的修正

- 消息流与回复协议只描述 find_tools 按需官方选择及跨消息工具池，不并存旧预选算法。
- Skill/MCP 资源总览承接到现行生命周期与两层设计，去掉内存草稿、全局 Skill 更新时间缓存、发布旧接口和旧自动探测说明。
- 模型、Skill、附件、产物及侧栏说明区分首次初始化和已登记保留数据升级，去掉阶段性清库、清凭据及删除表步骤；没有新增或执行任何升级。
- 访问提示只描述实际挂载的 AccessNoticeGate，删除首页完整声明已经展示的错误说法；不改闲置组件或测试。
- 附件身份与资源说明统一为 Cookie 会话及服务层归属校验；架构不再依赖本地草稿中的框架行号。
- 索引只列当前实现；任务验收保留当前测试映射，删除中间报告/通知/详情阶段的验收流水账。现存数据库升级、动态工具入口和有效测试不删除。

## 验收与决策

文档相对链接、必需地图条目、当前设计的显式源码/测试路径与文档空白检查均通过。对照整理前 SHA-256 清单，src、frontend、tests、scripts、ops 和内置模板文件完全未变化，上一轮未提交清理保留。

本轮只做文档验证；没有重新运行完整业务测试、生产构建、外部 API、真实数据库或浏览器。前一轮全量检查结果保留日期，不冒充本轮实测。

## 回退

Git 跟踪文档按本轮文档补丁的对应 hunks 逆向恢复，保留前一轮未提交源码与文档改动。note 被 Git 忽略，不能依靠 git restore 找回，须从整理前的本地临时副本恢复需要的文件。本轮没有提交、推送、部署或数据操作。
