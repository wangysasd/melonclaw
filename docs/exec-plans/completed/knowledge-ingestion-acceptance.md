# 知识库导入验收

状态：本轮已完成；核心导入API及页面闭环通过，剩余真实观测边界明确保留
开始日期：2026-11-06

## 目标与执行约定

验证现有导入代码在真实开发环境的行为，并将真实外部调用、浏览器操作、隔离数据库事务和模拟故障的证据分别记录。用户授权构建案例并由 GPT-6 Luna 子智能体执行。只创建带 `KBACC` 标识的验收数据，不清库、不升级、不修改共享模型配置、不影响已有业务资料。服务重启只在独立测试进程或可确认无活动业务的受控环境执行；否则标为未执行，不以单元测试代替。

样本、逐项 JSON 证据及浏览器截图放在 Git 忽略的 `output/knowledge-acceptance/` 与 `output/playwright/`。持久验收结论回填本文件。不得输出配置凭据、Cookie、完整数据库 URL、原始 SDK 异常。

## 案例与通过标准

| ID | 层次 | 输入/操作 | 通过标准 |
|---|---|---|---|
| A01 | 真实 API/模型 | UTF-8 TXT，中文、英文、emoji、组合字符 | 成功发布；正文保留标记；chunk 等于原文切片；1024 维有限非零向量；下载 SHA256 一致 |
| A02 | 真实 API/模型 | Markdown 标题、列表、表格 | 内容标记完整，至少一个 chunk，下载一致 |
| A03 | 真实 API/模型 | 两页文本 PDF | 两页标记及 page=1/2 均存在，method=text，未漏页 |
| A04 | 真实 OCR/模型 | 两页扫描 PDF | 两页均 method=ocr；日期、金额、页标记人工/程序核对 |
| A05 | 真实 OCR/模型 | 一页文本一页扫描 PDF | 页序、两种 method 和各页标记正确 |
| A06 | 真实 OCR/模型 | PNG、JPEG | 两种格式成功发布，核心数字/标记可核对 |
| A07 | 真实 API | 空文件、unsupported.exe、超限文件 | 明确 4xx，不能发布；超限仅针对验收请求，不改全局限制 |
| A08 | 真实 API/解析 | 损坏 PDF、加密 PDF | 作业失败，无 active revision/正文/chunks |
| A09 | 真实 OCR | 文本页加空白页 PDF | 按当前契约整次失败，不能部分发布 |
| A10 | 真实 API | 同 request_id 重放相同上传，再改内容 | 重放返回同 job/document/revision；不同内容冲突 |
| A11 | 真实 API/模型 | 发布 A，替换损坏 B，随后替换有效 C | B 失败后仍下载 A；C 成功后有效指针及下载变为 C |
| A12 | 真实 API | 对失败作业重试，重放重试 request_id | 新 job，沿用未发布 revision；重放无重复 job；损坏输入仍失败 |
| A13 | 真实 API/执行器 | 长扫描文档处理中取消并立即重试 | cancelled token 失效，无迟到发布；同文档执行不重叠；无法观测的槽状态单列 |
| A14 | 真实 API/权限 | 验收 viewer/editor/其他租户用户 | viewer 可下载不可写，editor 可上传，跨租户详情/下载/修改拒绝 |
| A15 | 真实 API/执行器 | 验收 editor 上传后处理中撤权 | 不得发布；只操作本轮验收成员 |
| A16 | 隔离 PostgreSQL | 发布时审计失败 | 正文/chunks/指针/job 成功/事件一起回滚 |
| A17 | 隔离 PostgreSQL | 取消旧 token、重试新 token、重复发布 | 旧 token 拒绝、新 token 不同、已发布不可改写、失败保留旧版 |
| A18 | 模拟故障 | OCR 截断、空结果、向量维度/NaN/零向量 | 明确失败，不作为真实服务质量通过 |
| A19 | 独立进程/隔离环境 | 正常停服与异常重启 | shutdown/restarted 收敛，旧 running 不自动重跑，queued 可继续 |
| A20 | 真实浏览器 | 新建库、上传 TXT、刷新状态、下载、失败提示 | 真实页面与 API 一致；保存截图；不以组件测试代替 |

## 步骤

- [x] 构建 15 个样本及 manifest，核对 PDF 文本层、扫描页与 8 页渲染。
- [x] Luna 执行真实 API、模型与数据一致性验收，正常发布失败及依赖项阻塞如实记录。
- [x] Luna 执行隔离事务、故障注入及执行器生命周期验收。
- [x] Luna 执行真实浏览器流程，保存 10 张截图。
- [x] 汇总逐项通过/失败/阻塞，保留复现信息和验收数据定位。

## 首轮结果（修复前）

首轮验收未通过。2026-11-06 开始执行，2026-11-07 继续核对证据；由三个 GPT-6 Luna 子智能体分别执行 API、隔离数据库与浏览器验收，主智能体复核。测试跳过、未观测和环境阻塞均不算通过。发现问题先记录，未经复测不改为通过。

### 逐项结论

| 案例 | 结果 | 实际证据与边界 |
|---|---|---|
| A01 | 失败 | 正常 TXT 经真实 API 入队，解析与嵌入检查后在 publishing 失败；没有可下载版本。完整 Processor 复现确认下述文件打开错误。 |
| A02–A06 | 阻塞，未执行完整闭环 | Markdown、文本/扫描/混合 PDF、图片的成功发布均依赖 A01 的共同步骤，未反复调用外部模型制造同一失败。真实 OCR 质量尚未验收。 |
| A07 | 通过 | 空文件、不支持格式、50 MiB + 1 byte 的 TXT 均返回 422；首次以 .bin 测尺寸的错误 fixture 不计尺寸验证，通过项来自更正后的 .txt。 |
| A08 | 通过 | 损坏和加密 PDF 入队后在 parsing 明确失败，未发布。 |
| A09 | 阻塞，未执行 | 空白页样本已准备，需修复发布阻断后单独验收；不能以共同 TypeError 导致失败充当空白页检测通过。 |
| A10 | 通过幂等验证 | 相同 request_id 和文件返回同 job/document/revision，改内容重用标识返回 409；该作业的正常发布不在此通过结论内。 |
| A11 | 阻塞 | 开发环境没有本轮成功发布的 A 版本，真实替换及原件下载闭环未验；隔离数据库的旧版保留测试另计 A17。 |
| A12 | 通过重试幂等验证 | 损坏输入重试产生新 job、沿用未发布版本；重复 retry 标识返回同 job，仍在 parsing 失败。 |
| A13 | 部分通过 | 首次只取消 queued，不计运行中取消。补测确认 running/started/token 后取消，终态 cancelled/token 清空；立即重试和重复请求正确。真实 API 无法观测本地执行槽释放时机，该部分只由已有受控测试覆盖。 |
| A14 | 部分通过 | 验收 viewer 的当前身份和租户核对正确，详情和文档列表 200、role=viewer，上传 404；跨租户身份核对后读取/写入拒绝。viewer 下载因没有已发布版本而阻塞。 |
| A15 | 通过撤权终止验证 | editor 作业已 running 且 token 存在时撤权，作业变 cancelled、token 清空、无有效版本。 |
| A16 | 隔离 PostgreSQL 部分验证 | 审计写入注入失败后，明确检查正文为 0、有效版本为空、job 仍 running；原测试未逐项查询失败后的 chunk/revision/event，不将这些细项记为已观察。 |
| A17 | 隔离 PostgreSQL 通过 | 旧 token 不能发布，重试获得新 token；失败保留旧版正文与有效版本，已发布作业不能再次发布。 |
| A18 | 模拟故障验证通过 | OCR 截断/空结果、错误向量数量/维度、NaN/Inf/零向量等被拒绝；不代表真实模型内容质量。 |
| A19 | 隔离进程与数据库验证通过 | 真实执行器进程正常关闭得到 service_shutdown；SIGKILL 后重启得到 service_restarted；queued 被重新领取。暂停中的处理函数使用模拟实现，queued 的故意配方不匹配最终失败，仅证明重新执行，不证明导入成功。 |
| A20 | 正常导入失败，页面交互部分通过 | 浏览器建库、上传入队、状态刷新、损坏文件重试及删除确认框可操作；TXT 失败且无下载按钮，下载 SHA 验证阻塞；未把组件测试当浏览器证据。 |

额外验证：隔离数据库用 4 个真实 chunk 记录和每条 1024 维合成向量执行批量发布成功，正文、顺序、有效版本和单条发布事件一致；这排除了该测试输入下批量向量 CAST/insert 的疑点，但不代替真实 Processor 链路。

常规 `scripts/check.sh` 通过。另行连接独立 PostgreSQL 的定向测试为 **67 passed、1 failed**；失败为 `test_permission_transfer_models_and_cache_publication` 仍期待新库引用 parser 模型，因而期待删除 parser 被拒绝。当前设计已明确新库 `parser_model_id` 为空，故属于过时测试断言，保持失败记录、不改测试掩盖，后续按新契约修订（技术债 KB2）。

验收 API 库保留 7 个文档、10 个终态作业，queued/running 为 0；浏览器库另保留 TXT 与损坏 PDF 失败记录。所有用户/租户/库均带 KBACC 标记，定位清单保存在 `api-context.json`。未修改业务代码、共享模型配置或数据库结构，也没有清理原有业务资料。

隔离测试使用临时 PostgreSQL 18.4、pgvector 0.8.7，测试后实例、临时数据库目录、运行数据目录和临时连接 URL 文件已清理。开发后端保留当前代码的运行实例，未再次重启。最终文档链接校验 2 passed，`git diff --check` 通过。

### 已确认的发布阻断

`src/melonclaw/services/knowledge_processing.py:172` 使用 `raw_path.open("w", opener=...)`，但 `pathlib.Path.open` 不接受 `opener` 参数。正常 TXT 已通过真实 API 和真实浏览器分别复现：进入 publishing 后变为 failed，没有有效版本，因此不能下载。最小复现返回 `TypeError: Path.open() got an unexpected keyword argument 'opener'`。

Luna 另外执行真实 `KnowledgeProcessor`、真实隔离 TXT 解析子进程与模拟 embeddings 的完整处理路径：到达 chunking → embedding → publishing，抛出相同 TypeError，`knowledge_publish` 调用次数为 0；HTTP 客户端及临时目录正常清理。此证据证明错误发生在数据库发布事务之前，不能把“无正文/chunks/有效版本”说成已验证事务回滚。事务回滚由独立 PostgreSQL 测试证明。

本轮保留应用代码，不通过绕过文件写入或手工写业务数据制造成功发布。应修正文件打开方式，并增加涵盖 Processor 文件写入边界的永久回归用例，再重新执行正常导入、下载和替换闭环。记录见技术债 KB1。

### 证据位置

- `output/knowledge-acceptance/manifest.json`：15 个初始样本的摘要与 PDF 文本层检查。
- `output/knowledge-acceptance/evidence/api-results.json`：真实 API 操作与数据库只读核对。
- `output/knowledge-acceptance/evidence/browser-results.json`、`browser-report.md`：真实浏览器结果；截图位于 `output/playwright/knowledge-acceptance/`。
- `output/knowledge-acceptance/evidence/publish-blocker.json`、`processor-repro.json`：最小复现及完整 Processor 复现。
- `output/knowledge-acceptance/pytest.log`：真实隔离 PostgreSQL 定向测试输出。
- `output/knowledge-acceptance/evidence/isolated-report.md`、`isolated-results.json`：隔离测试范围、实际结果和清理确认。
- `output/knowledge-acceptance/isolated-executor-evidence.json`：独立执行器停服/重启和四分块发布断言。
- `output/knowledge-acceptance/evidence/full-check.log`：常规检查结果。

上述输出在 Git 忽略目录中保留本地证据；长期结论以本文件及技术债清单为准。样本构建与各轮操作脚本保存在同一输出目录，脚本只用于本次验收，不作为应用运行入口。

### 环境准备

- 开发实例最初只加载管理路由，没有文档导入路由，首次上传 404 属于旧进程问题。检查聊天 pending、附件 processing、定时任务 queued/running、知识作业 queued/running 均为 0 后，受控停止旧后端并启动当前工作区代码；就绪探测与上传路由均确认恢复，没有执行数据库升级或清库。
- 样本正文均为合成非敏感内容。TXT 覆盖中文和 emoji；OCR 样本主要为英文、数字与简单表格，不代表复杂中文研报、图表、多栏文档的质量验收。
- 常规 `scripts/check.sh` 通过；该结果与另行执行的真实 PostgreSQL 验收分别统计，不能代替集成测试。

## 2026-11-07 修复后复验

下列为独立修复后记录，前文保留最初失败证据。`Path.open` 已改为支持opener的内置open，UTF-8和0600权限保留；完整Processor回归覆盖发布与清理。新索引使用jieba搜索模式，处理配方版本2，不静默重写既有版本。

Luna真实API复验确认：A01–A06（含PNG/JPEG）均成功发布，正文/标记、PDF页码和解析方法、chunk原文切片偏移、文本hash与有限非零1024维向量检查通过；各格式下载SHA256与上传原件一致。A09空白页按契约失败且无发布正文/分块/active指针。A11损坏替换失败保留原有效版本和原下载hash；成功替换切换新版本并匹配新下载hash。A14 viewer可下载且hash一致但不可上传，editor可上传发布/下载，跨租户详情/下载/修改均404。逐项证据在本地 output/knowledge-acceptance/evidence/postfix-api-results.json。浏览器成功路径和查询闭环继续验收，尚不把所有案例标为通过。

修复后补充：A16在真实隔离PostgreSQL中补齐正文/chunks/published_at/active指针/发布事件回滚断言，随后正常发布成功；全知识库85项通过。A20在真实页面上传长TXT，状态完成并出现可用版本，点击下载原件后与本地样本SHA256完全一致。真实聊天来源链接打开当前版本纯文本抽屉。原文分页/历史版/404/身份切换取消由组件测试与真实PostgreSQL版本读取覆盖，当前真实来源96字，未把未触发的浏览器分页分支算作通过。A13真实外部本地槽释放精确时序仍未直接观测，受控协程测试与既有取消/重试证据保留；不影响本轮核心发布闭环结论。复杂中文研报OCR与大规模召回不在合成烟测结论内。
