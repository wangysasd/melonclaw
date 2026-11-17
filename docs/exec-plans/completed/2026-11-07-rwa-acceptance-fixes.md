# RWA 真实资料验收两项修复

状态：已完成
开始日期：2026-11-07

## 目标

修复 RWA 知识库真实资料验收发现的两个问题，并用回归测试与真实环境验证：
1. `RWA发行落地流程08-25.pdf` 26 页导入在 parsing 阶段失败，且错误不可定位；
2. ERC 标准对照与香港稳定币赎回回答只有 `#source-*` 锚点，没有可打开的版本化来源。

判断标准：目标失败文档重试成功并发布 26 页正文/分块、原第 3/13 页标记为 OCR；两题答案均含可点击、可回读 revision 与 offset 的链接，NAV 拒答不退化；不清库、不删文档、不改模型或 Key；`scripts/check.sh` 通过。

## 背景

验收证据见 `output/knowledge-acceptance/rwa/report.md`、`search-results.md`、`chat-results.md`、`cases.md`。
现有 RWA 库有 6 份成功发布资料和 1 份失败资料，失败版本保留在库中。

真实复现结果（一次性诊断脚本只输出分类元数据，不输出凭据、URL 或正文）：

- 26 页中实际有 5 页文本层不足 80 个字母/数字（第 1、3、11、13、17 页），都走逐页 OCR；文件大小、页数、渲染像素均未超限。
- 第 13 页用固定 grounding 提示词只返回 `<|ref|>image<|/ref|><|det|>...<|/det|>` 版面块，剥离标记后 **0 字符**。`recognize_page` 当时只校验原始字符串非空，随后 `assemble_pages` 判定空白页并整次失败。同版本重试复用同一原件与配方，必然复现。
- 同一固定服务、模型、端点与凭据改用官方通用 OCR 模板对该页返回 205 字符正文；其余 4 页 grounding 结果非空。
- 失败链路把异常压成 `processing_failed` + 固定摘要，不更新 stage、不带页码，子进程错误码与 traceback 都被丢弃，因此 API 无法定位页或环节。

第二个问题的根因是提示词冲突：`RESULT_OUTPUT_GUIDANCE` 要求「编号引用：正文写 `[1](#source-1)`」且 `url 只用实际 HTTP(S) 地址`，而知识库 `source_url` 是同源相对路径；模型在多文档对照/法规类答案里选择编号来源块，相对路径既不能进 `url`，也不会变成内联链接。`KNOWLEDGE_GUIDANCE` 虽要求用 `source_url` 输出 Markdown 链接，但没有声明冲突时的优先级。

## 步骤

- [x] 真实复现并定位第 13 页 grounding-only 输出，核对备用模板有效性。
- [x] `core/knowledge_ocr.py`：固定协议内增加「grounding 剥离后无正文 → 同页同服务通用 OCR 一次」的有界回退；拆分 HTTP/超时/截断/空正文等安全分类；返回结构化页结果。
- [x] `parsers/knowledge_worker.py` 与 `services/knowledge_processing.py`：页级错误码与页码跨进程透传；空白页、OCR 失败、渲染异常都生成含分类与页码的安全摘要。
- [x] `services/knowledge_executor.py`、`repository/knowledge_documents.py`：失败时把分类与页码写入现有 `error_code`/`error_summary`，文档列表一并返回分类码。
- [x] `core/prompts.py`：知识库引用一律用服务端生成的内联 Markdown 链接，明确排除编号来源块与 `#source-*` 锚点，并声明冲突时以知识库规则为准。
- [x] `services/knowledge_search.py`、`tool/knowledge.py`：检索/打开原文结果新增 `citation` 字段（由 `source_url` 派生、标题转义），工具回执要求原样复制。
- [x] 补后端、前端回归测试；真实重试目标文档并核对 26 页；真实聊天端到端复验。

## 验收标准

- 目标文档同版本重试 `succeeded`，`active_revision_id` 为原版本；正文/27 个 chunk 覆盖 1–26 页；第 3、13 页 `method=ocr`；其它 6 份文档有效版本 ID 不变。
- 若外部 OCR 仍失败，作业须给出不含凭据/正文的分类码与页码，且不得发布部分正文；不得跳过页面或伪称成功。
- ERC 对照、稳定币赎回答案均含 `/api/knowledge-bases/{kb}/documents/{doc}/text?revision_id=...&offset=...&limit=...` 链接；每条链接 200、revision 与文档当前版本一致、回读 offset 一致；不得只有 `#source-*` 锚点或虚构 URL。
- NAV 拒答等既有行为不变；`scripts/check.sh` 全部通过。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-07 | 只在 grounding 剥离后为空时回退一次通用 OCR，不改变其它页结果 | 同一固定服务/模型/端点/凭据，有界、真实识别；既修复图表页，又不把版面标记当正文 |
| 2026-11-07 | 不提高 `parser_version`，不改写处理配方 | 模型、端点、渲染与分块参数都未变；目标失败版本可直接重试，避免重新上传 |
| 2026-11-07 | 错误分类与页码写入现有 `error_code`/`error_summary`，不新增表或升级 | 两字段无取值约束、长度足够，`error_summary` 已在文档页展示；无需 db-update |
| 2026-11-07 | 由服务端生成 `citation` Markdown 链接供模型复制 | 模型不必自行拼 URL，避免虚构或省略 revision/offset；`source_url` 仍是唯一来源 |
| 2026-11-07 | 不修改 `melon-result` sources 块的前端 URL 规则 | 该块用于外部 HTTP(S) 来源；知识库引用统一走已支持版本抽屉的内联链接 |

## 风险与依赖

依赖开发侧固定 OCR/嵌入服务与数据库默认聊天模型，真实调用产生服务费用。通用 OCR 回退仍是同一供应商的生成结果，复杂中文图表页可能识别不全；若服务返回限流、过载、超时或两次均无正文，作业按页失败并给出安全分类，不发布部分正文。仅单 Web 进程；重启前须确认无活动作业。重启使用同一外部数据根，不改 `.env`、模型配置或数据库结构。

## 验证记录

- 定向后端回归 59 passed（导入、页级回退、分类、26 页完整性、失败不发布、引用生成与提示词）；前端引用 6 passed；`ruff check src tests` 通过。
- 真实导入：`retry` HTTP 200，作业 `succeeded/publishing`，`active_revision_id` 为原版本；正文 7006 字符、`char_count` 一致；27 个 chunk 覆盖 1–26 页，第 1/3/11/13/17 页 `method=ocr`；`ocr.json` 记录 5 页且同时含 `grounding` 与 `plain_ocr`；库内发布数 6→7，其它 6 份有效版本 ID 完全一致。
- 真实聊天（现有数据库默认模型、每轮显式选择 RWA）：6 题均 `source_api_verified=true`，ERC 对照 8 条、稳定币赎回 16 条版本化链接，`#source-*` 锚点 0；66 条链接逐条回读，kb/文档/revision 一致、offset 与响应一致、limit 在范围内；NAV 拒答保留。RWA 流程题不再拒答，改为依据新发布资料作答，属于修复后的预期变化。
- 原始验收证据保留为 `*-before-fix.*`；修复后结果在 `output/knowledge-acceptance/rwa/import-after-fix.json`、`chat-results-after-fix.*`、`citation-offsets-after-fix.json`。
- 最终 `scripts/check.sh` 结果见本轮交付说明；无 schema 变更，不需要 `db-init` 或 `db-update`。
