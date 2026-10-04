# 对话产物与 HTML 预览

状态：已实现
日期：2026-10-03

## 背景与目标

AI 交付 HTML、图片或文件时，正文链接、回复末尾卡片和当前对话的产物列表进入同一个右侧面板。用户可以继续聊天并查看文件，浏览器刷新后从持久消息恢复交付入口。

## 方案与关键选择

- 文件仍在普通会话或项目工作区的 `/outputs/` 下，交付引用保存为可重建的 `conversation_artifacts` 数据库投影，不创建文件快照或版本副本。每次打开、刷新和下载都读取当前文件；同路径文件被覆盖后，历史卡片也指向更新后的内容。
- `services/result_index.py` 使用 Markdown 语法树，只提取完成的最终回答里的成果链接、图片引用和有效 `melon-result` file/image 围栏。代码、未闭合围栏、来源块、外部 URL、过程和临时文件不进入列表。路径约束与实际打开文件共用 `storage/results.py` 的验证。
- history 与 completed（含幂等回放）返回相同 `artifacts` 字段，原始正文和业务表不变。完整会话列表由 `ResultFileService.index` 读取交付投影，以完整引用去重并保留最近交付来源。索引请求只读取交付行，不加载消息正文、执行轨迹或 Checkpoint，也不扫描工作区。
- 明确引用但暂时不可读的文件保留错误卡片和重试，不伪造成功。文件名称、大小与预览类型来自服务器。

## 用户交互

最终回复中的 file 结果块统一转为末尾文件卡片；图表和图片继续在正文中展示。普通成果链接和卡片打开同一面板，生成结束不自动展开。

顶部入口已扩展为「文件」，默认浏览实际会话／项目工作区；「本对话产物」查看完整会话交付列表。消息链接／卡片仍直达预览，返回进入所在目录并选中文件。面板保留刷新、下载、放大、关闭和定位到回复；定位尚未加载的消息时，只读分页补入聊天历史，不覆盖当前 SSE 消息或执行 Agent。目录与产物的区别见 [文件浏览器设计](workspace-file-browser.md)。

桌面为可调整宽度的右侧面板，左侧聊天保持可操作；1100px 及以下覆盖聊天区域。覆盖或放大时隐藏区设置 inert，面板提供焦点管理、键盘关闭和基本焦点循环。会话或用户切换通过组件身份重新挂载清空面板；正文、控制文案沿用 14px，窄屏主要按钮至少 44px。

HTML 和 Markdown 支持预览／源码切换。HTML iframe 在源码切换和后续交付时保持挂载；同文件再次交付仅提示「此文件已重新交付」，用户明确刷新后才重新读取，避免丢失筛选和输入状态。这个提示表示收到新交付记录，不宣称文件字节一定变化。

## 数据与事件流

受审批工具生成文件 → 解析最终正文的明确交付引用 → 同一事务保存完成回复并更新交付投影 → completed/history 返回引用 → 消息卡片与链接进入 ArtifactWorkspace → 服务端校验归属并读取当前文件。

`conversation_artifacts` 按会话与规范引用摘要保存最近交付的 message_id、message_seq、created_at 和顺序。引用摘要使用 SHA-256，避免长路径成为数据库索引键。只有助手完成事务可以写入：正文与索引一起提交或回滚，CAS 冲突不产生交付；较旧消息迟到完成时不能覆盖较新的来源。

`ArtifactWorkspace` 请求会话产物索引，并合并当前页面收到的新交付。元信息请求按引用在当前身份和交付批次内合并；预览内容只在打开、选择文件或明确刷新时读取，不跟随流式文本重复加载。内容 Blob 在关闭、切换和刷新后释放。

## API

- `GET /api/conversations/{id}/artifacts?user_id=...`：完整交付索引，items 包含 ref、message_id、created_at。只读、解析有效用户及会话／项目归属。
- `GET /api/conversations/{id}/result-files/html-preview?user_id=...&path=/outputs/...`：读取真实 `.html`/`.htm`、不超过 2,000,000 字节的 UTF-8 文件；再次校验读取到的大小与编码，响应带独立预览 CSP。
- 原 result-files 元信息增加 `preview_kind=html`。原 content 接口即使请求 preview=true，也不内联 HTML；仍是 attachment 下载与严格 sandbox CSP。

## 失败与安全边界

HTML 是自包含页面：允许内嵌 CSS、JavaScript、data 图片／字体／媒体；不提供 CDN、相对资源文件树或应用 API 桥接。iframe 仅授予 allow-scripts，不授予 allow-same-origin、表单、弹窗、下载、模态窗口或主页面导航权限。CSP 限制资源加载、脚本连接、子 frame、对象、base 和表单目的地。

独立预览响应同时含 HTTP CSP 和位于原始内容之前的可信 meta CSP；后者保证通过 Blob 加载时仍保留资源策略，iframe 属性另外强制 sandbox。源码显示去除可信前缀后的原文件内容，作为 React 文本渲染。预览不允许 eval；响应权限策略与 iframe allow 同时拒绝摄像头、麦克风、定位和剪贴板能力。

浏览器 sandbox/CSP 不是完整网络或 CPU 沙箱，不能承诺约束所有 iframe 自身导航或无限循环脚本。共享部署仍需要可信身份、独立预览隔离方案及执行环境隔离；开发 user_id 与 LocalShellBackend 的既有边界不变。

新增接口是逐次归属校验的只读操作，无需 HITL；没有新增 Agent 工具或 PTC 能力。文件生成继续使用现有审批链路。错误通过统一错误映射脱敏，不输出宿主机路径或凭据。

## 运行步骤与预期

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw-web
npm --prefix frontend run dev
scripts/check.sh
npm --prefix frontend run build
```

新增直接依赖 markdown-it-py，锁文件和 pip 清单同步。新增 `conversation_artifacts` 表，现有表列不变，无需清空数据库。暂停聊天写入后执行 `uv run melonclaw-db-init`，命令通过 `metadata.create_all` 建表，并从已完成正文流式、分批、原子重建投影；然后重启后端。重建失败会回滚整个投影替换，日常查询不回退到历史扫描。

预期：让 Agent 生成自包含 HTML 并给出 /outputs/ 链接，回复末尾出现一张文件卡片。链接、卡片和产物列表打开同一预览。修改同路径文件后旧入口读取当前内容，已打开页面仅在明确刷新后更新。

## 验证记录

- 后端测试覆盖真实 Markdown 解析、代码／来源／无效围栏排除、编码路径／路径穿越、附件引用、直接索引读取与最近来源、会话拒绝，以及真实 HTTP HTML/下载响应、当前文件覆盖、大小与编码失败。
- 前端测试覆盖真实 Markdown 和卡片接入、末尾去重、列表恢复与合并、切换会话清空、源码切换保留 iframe、后续交付提示与明确刷新、下载错误与重试。
- 历史定位回归验证跨页加载、不重复插入、保留进行中的 SSE 消息，以及切换会话后忽略迟到响应；放大／覆盖模式定位时先收起面板，再滚动和聚焦原回复。
- Orca 浏览器使用真实前端组件和真实 FastAPI 成果服务／路由，搭配临时文件及内存仓库夹具：验证 HTML 按钮可操作，预览 origin 为 null，父 DOM 读取与 fetch 被拒绝，源码切换保留同一 iframe，再次交付不重载，明确刷新读取修改后的文件。
- 桌面布局和 390px 窄屏已检查：无横向溢出，覆盖状态设为 dialog／inert，主要按钮至少 44px；关闭／定位恢复聊天操作和焦点。夹具没有修改真实业务数据库，也没有调用外部模型；模型实际写文件及审批交付仍依赖已配置模型和运行环境。
- `scripts/check.sh` 全量通过，最终 iframe 权限补充后前端检查再次通过；前端生产构建通过。本地后端已重启至 ready，前端 8001 与新增接口已确认可用。完整记录见 [执行计划](../exec-plans/completed/2026-10-03-conversation-artifacts.md)。

### 2026-10-04：交付索引性能修复

- 读取测试禁止调用消息分页和 Markdown 解析，并验证仓储查询不包含 `chat_messages`，重新校验用户和有效项目归属。
- 真实隔离 PostgreSQL Schema 验证同文件重复交付、迟到旧消息、CAS 冲突、索引写入失败时回复回滚，以及重建失败时原索引保留。
- 加入 20,000 条无交付的完成消息后，重建仍恢复原交付，读取只返回一条交付项；常规读取无需处理这 20,000 条正文。

## 当前范围

首版汇总文件交付，支持 HTML、图片、PDF、文本、代码、JSON、CSV 和 Markdown 的基础预览。Office、压缩包等只显示文件信息及下载；结构化图表、表格和 Mermaid 保留原有正文展示，后续可接入面板。
