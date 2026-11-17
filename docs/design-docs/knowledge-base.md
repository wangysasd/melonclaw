# 个人与共享知识库设计

状态：资料导入、混合检索与普通聊天引用已实现。
现行契约整理日期：2026-11-17。

本文维护当前行为与安全边界。阶段方案、早期探针和有日期的验收过程见[历史记录](../exec-plans/completed/2026-11-17-knowledge-design-history.md)，不作为当前配置依据。实现字段与约束以文中链接的 schema、仓储和测试共同核对，变更时直接修订对应章节。

## 目标与范围

知识库让资料一次导入后可跨会话检索、共享和引用版本原文。聊天附件用于当前对话，Memory 用于偏好和长期记忆，三者生命周期独立。

- 任意有效用户可建库。共享可选所有人、同租户或指定人，新建默认同租户可查看。
- 导入 PDF、Word、PowerPoint、常见图片、TXT 和 Markdown；格式与处理见[文档导入](#文档导入)。知识库白名单独立于聊天附件。
- 管理页提供 vector、keyword、hybrid 三模式对照；普通聊天固定使用混合检索，可有限补查和读取命中上下文。
- 同一库一次处理一份资料，其他库可并行；取消后等待实际执行退出再释放占用。
- 不提供 URL 抓取、外部知识库连接器、单文档 ACL、匿名公开下载、Agent 知识库写工具或后台定时任务读取。未引入独立 Worker、ANN、重排模型或新检索框架。

## 实现入口与数据流

以下路径相对 `src/melonclaw/`，业务继续复用既有包和数据库事务，不建立独立应用。

| 责任 | 入口 |
|---|---|
| Cookie API 与严格输入 | `api/routes/knowledge.py`、`core/knowledge.py` |
| 库、成员与权限事务 | `services/knowledge.py`、`repository/knowledge.py` |
| 上传、幂等和文档版本 | `services/knowledge_documents.py`、`repository/knowledge_documents.py` |
| 执行与解析编排 | `services/knowledge_executor.py`、`services/knowledge_processing.py`、`parsers/knowledge_worker.py` |
| 固定服务与内部引用 | `core/knowledge_embedding.py`、`core/knowledge_ocr.py`、`repository/knowledge_embedding.py` |
| 原件和私有派生文件 | `storage/knowledge.py` |
| 查询编排与 SQL | `services/knowledge_search.py`、`services/knowledge_query.py`、对应 `repository/` 模块 |
| 聊天工具和结构化来源 | `tool/knowledge.py`、`output/knowledge_sources.py` |

导入链路：授权并保存原件 → 同事务登记文档/版本/排队作业 → Web 内执行器领取 → 本地文本读取或固定 OCR → 原样分块 → 固定嵌入 → 同事务发布正文、chunks、版本指针、作业终态与审计。

查询链路：本轮选库与当前授权 → 有效版本范围内两路召回 → RRF 融合与有界筛选 → 重验权限/版本 → 返回来源 → 按同一版本读取原文。耗时外部调用不持业务事务锁。

## 共享范围设计

知识库归唯一 `owner_user_id`，不保存租户字段。共享语义按所有者当前 `users.tenant_id` 和有效状态实时计算；点名授权可以跨租户。

| share_scope | 自动获得查看权的人 |
|---|---|
| everyone | 任意有效用户，且其租户启用 |
| tenant | 当前与所有者同租户的有效用户 |
| selected | 仅点名的成员；无成员时仅所有者和系统管理员可访问 |

新建页面与服务默认 tenant；数据库列默认 selected 用于保守初始化，两者不可混为一谈。同租户不会展开写入成员表。三种范围都可另行授予 viewer/editor，范围只授予查看权，编辑者始终点名。

创建/编辑可以指定最多 100 位查看者，已有编辑者保留。指定查看者与库配置同事务保存，失败全部回滚。有效角色按所有者、成员角色、范围查看者计算；system/admin 具有单独的管理能力。

成员页区分默认查看范围、单独授权和所有权。范围须保存才生效，切换到指定人需确认；移除成员行不消除范围或系统管理员身份带来的访问权。所有权转让仅所有者可操作：同事务移除目标用户的成员行、将原所有者设为 editor、更新所有者，并核对目标名下库名唯一。

## 授权与账户变化

| 操作 | viewer | editor | owner | system/admin |
|---|---|---|---|---|
| 列表、预览、检索、聊天引用、原件下载 | ✓ | ✓ | ✓ | ✓ |
| 上传、替换、删除文档，重试和取消处理 | — | ✓ | ✓ | ✓ |
| 库设置、共享范围和成员管理 | — | — | ✓ | ✓ |
| 删除知识库 | — | — | ✓ | ✓ |
| 转移所有权 | — | — | ✓ | — |

成员表只有 viewer/editor，owner 只存在库行。权限继承到库内文档、版本、分块、作业和下载，不叠加文件级权限。成员名单仅所有者和系统管理员可看；候选接口只返回有效用户 ID、中文名和租户名，每次最多 50 条。

- 访问者须有效且租户启用，库须 active。普通用户还要求所有者及其租户有效；system/admin 可访问所有者或其租户停用的活动库。
- 换租户保留点名授权，不批量取消作业；tenant 范围按所有者当前租户重算。转让也不批量取消作业，最终发布仍重验当时权限。
- 删除用户前须转让或逻辑删除其拥有的库；删除时清理相关成员授权并取消相关作业。用户/租户停用取消相关作业，但保留成员授权。暂停的库不因账户迁移自动恢复。
- 账户、授权、转让、删除、发布采用一致锁顺序：参与者用户 → 库 → 文档 → 版本 → job，各层按 ID 排序；锁后重验权限。账户管理仍走既有 account_guard。

列表、数量和分页直接在 SQL 过滤。检索先授权，两路在 Top K 前应用同一库、当前租户、嵌入空间、有效版本与文档范围；网络后及返回前再验权限与版本。调用者不能覆盖强制过滤，不能仅凭 chunk ID 读正文。不可见返回安全的不存在结果；原文与原件不开放静态路径。

撤权能阻止后续检索、原文读取、下载和发布，但不能追回已经返回的响应、模型上下文、Checkpoint、历史消息或用户下载内容。材料与库描述均是不可信数据，不能授权工具或扩大能力。

## 模型与凭据

知识库嵌入和 OCR 由开发维护，用户和管理员均没有配置、选择、启停或测试入口。创建 API 拒绝 `embedding_model_id`，公共响应隐藏内部引用；不保存解析模型引用，也不解析聊天模型默认值。

| 配置 | 来源与用途 |
|---|---|
| `KNOWLEDGE_EMBEDDING_API_KEY` | 开发嵌入凭据 |
| `KNOWLEDGE_EMBEDDING_URL` | 开发嵌入端点 |
| `KNOWLEDGE_EMBEDDING_MODEL_NAME` | 开发嵌入模型名 |
| `KNOWLEDGE_EMBEDDING_DIMENSIONS` | 必须匹配当前 `vector(1024)` |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 OCR 凭据 |
| `KNOWLEDGE_OCR_TIMEOUT_SECONDS` | OCR 总超时，由 config 校验为正整数，默认 180 秒 |

配置统一从根 `.env` 经 `core/config.py` 进入 Settings。嵌入工厂不硬编码上述具体值，不读个人 Key、数据库供应商 Key 或 SDK 默认凭据；使用原始文本、float 编码、16 条批次、60 秒请求超时、零自动重试及禁用环境代理/重定向的客户端。

首次建库自动登记无凭据内部模型引用，仅满足外键及向量空间一致性；引用不参与实例配置。登记复用资源提交—发布边界，重复仅校验复用，冲突明确报错。内部稳定 key/UUID 不代表实际模型名。变更模型、端点或维度须评估既有向量空间；变更维度需独立 schema 升级与向量重建，不能静默混用。

OCR 地址固定在 `core/knowledge_ocr.py`，不传模型名，不引入用户配置的解析模型。配置不足时返回内部处理服务不可用，不能指导用户选择模型补救。初始化不创建默认模型或凭据。

## 数据与版本

数据库保持八张表与 `knowledge_search_chunks` 普通视图。完整字段、CHECK、外键与索引见 [knowledge_schema.py](../../src/melonclaw/database/knowledge_schema.py) 和 [knowledge_ddl.py](../../src/melonclaw/database/knowledge_ddl.py)，不在文档复制字段数量清单。

| 表 | 责任与关键边界 |
|---|---|
| knowledge_bases | 所有者、共享范围、配置、版本与 `active_pipeline_job_id`；库类型固定 vector，仅保存固定嵌入引用 |
| knowledge_members | `(kb_id, user_id)` 联合主键，角色仅 viewer/editor |
| knowledge_documents | 稳定文档身份、软删除、`active_revision_id` |
| knowledge_document_revisions | 不可变版本、受控原件键/哈希/大小、配方与发布时间 |
| knowledge_document_texts | revision 为主键的不可变全文、字符数与哈希 |
| knowledge_chunks | 原样正文片段、字符 offset、来源、词项与 vector(1024) |
| knowledge_jobs | 幂等键、运行状态/阶段/token、错误与 `execution_exited`；业务终态不等于本地执行退出 |
| knowledge_events | 同事务追加的白名单业务审计，不保存正文、凭据或任意请求对象 |

`knowledge_search_chunks` 仅暴露 active 库、未删文档及当前已发布版本。视图本身不替代 ACL，仓储查询仍携带完整强制范围。向量使用参数化 SQL，不通过框架自动建表/删表，不引入 PGVectorStore 或 ANN 索引。

chunk.content 必须等于同 revision 正文的 `[start_offset,end_offset)` 原样切片；字符数是 Unicode 码点，不是 UTF-8 字节或 JavaScript UTF-16 单元。仅记录可靠的页码与来源，不从模型猜测补齐结构。分块默认 800 字符、重叠 120，大小允许 100–2000，重叠不超过块大小的 20%；向量入库前核对数量、维度、有限值和非零值。jieba 搜索模式生成词项，数据库生成 `search_vector`。

上传/替换先保留旧有效版本，新的正文、全部 chunks、版本发布时间、有效指针、作业成功与发布事件同事务提交。事务核验当前角色、run token、未发布状态、空间和分块完整性；失败或取消不能发布部分结果。审计失败整体回滚，网络调用不持锁。

配方保存原始文件名、扩展名、解析版本和分块设置，不保存 OCR 端点或凭据。用户重试以新 request_id/job/token 绑定未发布 revision，沿用原 operation；相同幂等键须核对请求摘要且重新授权，不能重复创建。已发布正文与向量不可就地改写。

## 文档导入

TXT/Markdown 在本地读取，其余受支持格式整份提交 DeepSeek 官方 OCR。当前解析配方为 `parser_version=3`；旧配方重试明确拒绝，需要保留内容时重新上传。知识库不接受 XLSX/CSV/JSON/ZIP，不扩大普通聊天附件白名单。

### 文件与请求

允许的知识库文件为 PDF、DOC、DOCX、PPT、PPTX、PNG、JPEG、WebP、GIF、BMP、TIFF、TXT 和 Markdown。TXT 与 Markdown 由无网络子进程按 UTF-8 读取，`method` 为 `text`，不依赖 DeepSeek Key。其余类型在服务层把已保存原件整份 `POST` 到 `https://api.deepseek.com/v1/tools/ocr`。该地址写在 `core/knowledge_ocr.py`。请求只有两个字段：`purpose` 在前，图片为 `rich_layout_recognition`，PDF 与 Word、PowerPoint 为 `text_recognition`；`file` 在后。不传模型名、提示词或 `max_tokens`。

凭据使用进程 Settings 中的 `DEEPSEEK_API_KEY`，与联网搜索、网页读取和语音识别相同。Key 为空或只有空白时，本次识别归类为 `ocr_configuration`，不发起网络请求。

`KNOWLEDGE_OCR_TIMEOUT_SECONDS` 由 `core/config.py` 读入 Settings，默认 180 秒，须为正整数；它覆盖建立连接、上传和读取完整响应。收到保活换行也不会延长请求时限。超时时关闭本地 HTTP 响应流，作业记录 `ocr_timeout`。

导入配方不保存 OCR 地址或 Key。导入配方保存原始文件名、扩展名、解析版本和分块设置。嵌入模型名、服务地址和向量维度由进程环境配置提供，继续通过固定嵌入引用和向量列校验。重试按原版本的扩展名及分块设置处理，历史配方中的 OCR 与嵌入字段不参与执行。修改 `DEEPSEEK_API_KEY` 或 `KNOWLEDGE_OCR_TIMEOUT_SECONDS` 后需重启 Web。单文件仍默认 50 MiB，页数仍默认 100，正文字符上限不变。

### 完整性校验

响应去掉前导空白后解析。HTTP 200 且含 `error` 视为失败。页码使用接口 `index` 加 1，`pages` 必须从 0 连续且长度等于 `page_count`。任一页 `finish_reason` 不是 `stop`，剥离标记后没有正文，或页数超过本地上限，整次失败，不发布部分正文。只剥离全角竖线的 `<｜ref｜>` 与 `<｜box｜>`。原始输出写入该版本私有 `ocr.json`。索引用剥离后的正文，`source_locator` 只保留页码和 `method=ocr`。

PDF 的失败页若在原件中没有可提取文字且没有图片，错误码改为 `ocr_blank_page`，提示删除空白页后重新上传。原件读不出这一页时，仍使用截断或无正文分类。Word、PowerPoint、图片以及带图片的扫描白页不使用这个分类。

### 错误与重试

原件会发往 DeepSeek，按成功识别的页计费。客户端超时或断开后，远端仍可能完成并计费，因此失败不自动重试。

错误只使用固定中文分类，不记录供应商原文。作业的 `error_code` 用于排查，`error_summary` 给出用户可见的原因、已知页码和下一步建议。DeepSeek 明确报告的识别超时单独归类为 `ocr_recognition_timeout`。

OCR 文档进入分块、向量化或发布后失败时，记录重试可能再次计费的提示。界面在状态标签下方直接展示摘要，用户可自行选择重试或替换。重试从原件开始。本地并发仍由 `KNOWLEDGE_CONCURRENCY` 限制。

## 执行、文件与恢复

执行器随 Web 生命周期启动，持独立 PostgreSQL 会话锁；不支持多 Web 进程或滚动重叠部署。连接失效停止领取并取消本地执行，不重连假装继续持锁。正常停服记 `service_shutdown`，启动将遗留 running 收敛为 `service_restarted`；已开始作业不自动重跑，queued 在重新授权后可继续。

每库一次仅处理一份资料，其他库可在全局并发上限内同时处理。取消在短事务中置 cancelled、记录终态并清空 token；实际协程/子进程退出后才设置退出状态和释放流程占用。迟到结果不能发布。取消先提交则发布拒绝；发布先完成则取消不改成功终态。后台准备和处理受作业总时限约束。

原件位于 `MELONCLAW_DATA_DIR/knowledge/<kb_id>/<document_id>/<revision_id>/source`，OCR 原输出是同版本私有 `ocr.json`；临时输出在外部数据根的 `tmp/knowledge/`。路径由服务端生成，逐层拒绝符号链接与路径穿越，不接受主机路径，不写项目源码、聊天工作区或公开 static。文本解析子进程不接收应用凭据；子进程隔离不等于完整安全沙箱。

文件与数据库存在非原子边界。无主文件和旧版本物理回收尚无自动定期流程；维护时以数据库引用为准，跳过活跃上传/作业及未退出执行，不能按目录年龄直接递归删除。原件缺失须明确报告，不能用新版替代旧引用。备份和恢复同时覆盖数据库与原件，核对引用完整性。

## 混合检索与按需补证

### 混合流程与失败边界

普通聊天的 `search_knowledge` 只接受 kb_id、query、top_k 和可选 document_ids，不暴露 mode。服务与 `POST /api/knowledge-bases/{kb_id}/search` 仍支持 vector、keyword、hybrid，API 与管理页默认 hybrid，单路只用于明确对照和排障。

两路分别在完整 ACL、当前租户、有效版本、嵌入空间和文档范围内并行召回，每路最多 60 条，不共享数据库连接。向量余弦阈值只过滤向量分支；关键词仍使用参数化 `tsquery` 与 `ts_rank_cd`，不是 BM25。按 chunk_id 去重，使用 `sum(1 / (60 + rank))` 做 RRF，排名从 1 开始；融合最多 60 条。结果保留 vector/keyword 原始分数与排名、matched_terms 和 rrf_score，不相加异构分数，也不把 RRF 当答案可信度。

每分支固定 20 秒、整体固定 45 秒，包含换版后的一次有界重查。只有向量超时或向量服务故障允许降级为关键词结果，回执为 `status=degraded` 并给出 `vector_timeout/vector_unavailable` 和明确说明。关键词无有效词项记 `skipped/no_terms`，可使用向量结果；关键词正常空命中不是失败。两路正常空命中返回空结果；两路执行失败、关键词执行失败、权限、配置或版本错误均明确失败，单路模式绝不自动换路。取消会取消并等待分支与嵌入客户端清理。

最终仍做 ACL 和有效版本复核，并发换版最多重查一次。每片段最多 4000、最终合计最多 24000 字符。显式限定一份文档或候选实际只有一份文档时，可返回到 top_k；跨文档时保留每文档最多一半的来源多样性，不合并不同文档或 revision。高重叠片段只在同文档、同版本内去重，引用 limit 与实际返回片段长度一致。

关键词仍最多 8 个受控词项。选取时优先代码、含数字词项、年份和完整术语，复合词子词降权，原位置只作稳定同分排序，减少长问题末尾核心词被机械截掉；没有新增模型或实体识别调用。

### 查询组织与有限补检索

`KNOWLEDGE_GUIDANCE` 指导主 Agent：明确问题直接查；多轮追问按已知上下文补全主体、指标、时间和指代；保留公司名、代码、金额、年份与否定条件；不猜缺失信息，关键歧义再询问。比较/复合问题可拆少量子问题，但不为机械改写反复检索。改写不足时可回退原始问法。

证据不足分别处理：无相关证据则换词/改写；只覆盖部分子问题则补缺失对象；片段不完整则读上下文；资料矛盾则核对时间、版本和适用范围。提示词限制补检索 1～2 轮，运行时按每个 Agent 请求、每个知识库允许 3 次首次/子问题查询和 2 次补查，多库独立计数。计数保存在 AgentContext 的普通字典中，检查与递增之间没有 await，不在共享 Agent、provider、工具闭包或全局对象保存用户状态。SSE 重连不重放工具；新用户消息新建额度；显式恢复执行建立新的有界额度，旧工具结果不重复执行。

### 按需上下文

搜索默认只返回精简命中片段。需要补读时，`open_knowledge_document` 接收原结果的 document_id、revision_id、最多 4 个 chunk_ids 及 before_chars/after_chars，不要求模型计算 offset。仓储先验证当前权限以及所有 chunk 均属于该文档和 revision，服务计算并合并重叠/相邻区间，再逐段读取同一不可变版本；缺一个命中或版本不匹配即拒绝，不把不同版本拼在一起。

before_chars 默认 300、最大 1600，after_chars 默认 700、最大 4000；单区间最多 8000、单次合计最多 16000 字符。可可靠识别换行时裁掉首尾残缺段落并保留完整命中；无法识别时按受限原文范围读取，不伪造标题或表格结构。每段 citation/source_url 按实际读取 offset 和长度重新生成。上下文标记为 source_context，只是原证据扩展，不算新增独立证据。原 offset/limit 翻页与历史版本标识规则继续保留。

## 普通聊天与引用

输入区每轮显式重传 `knowledge_base_ids`（最多 20 个 UUID），默认空；ID 进入请求幂等摘要与业务用户消息 metadata。使用同一 request_id 改变选择会被拒绝；新轮不能从历史 ID 恢复授权。显式恢复沿用本轮选择，但工具继续按当前 ACL 校验。

选库后，普通主/子 Agent 直接可见 `list_knowledge_bases`、`search_knowledge`、`open_knowledge_document`。系统消息说明本轮库名、类型、ID 和有界描述，按不可信 JSON 数据呈现。未选择时工具发现不能替代选择，不能扫描全部私库；不保证每轮遍历所有选中库。

三个工具通过运行时注入身份与本轮选择，模型不能指定用户/租户。工具只读、有界、受控授权，免逐次 HITL，不进入 PTC 或后台任务。缓存 Agent 仅保留固定 provider，不在闭包或全局对象中保存请求身份、选择、查询或正文，不将知识库权限快照作为授权凭证。

检索和打开原文结果由服务端生成 `source_url` 与 `citation`，包含库/文档路径、revision_id、offset 和实际 limit。回答将引用附在相应论断旁，不由模型拼 URL，不用 `#source-*` 锚点冒充知识库来源。旧版仍存在且授权有效时读取旧版；删除、清理或失权后明确不可用，不用新版冒充旧证据。

完整工具结果在展示截断前由 `output/knowledge_sources.py` 提取、校验和脱敏。根 Agent 来源随 `assistant_steps`，子 Agent 来源随展示事件保存/重放；前端只从结构化 `knowledge_sources` 建立受控链接白名单，不扫描工具预览正文。元数据仅含标签、状态及版本化 URL，打开时再次授权。前端只展示受控纯文本，不执行原件脚本或宏。

## 页面与 API

管理页复用 WorkspaceHeader、14px 正文与统一确认交互，不显示模型字段。详情提供文档与检索测试，返回聊天保留草稿。成员页的范围、点名授权和转让分开维护。

检索测试仅显式提交时查询，支持三种模式、top_k 1–20、最多 20 份文档收窄；文档选项按需分页且只展示已发布资料。显示片段、来源、版本/字符范围、排名与分支诊断，不生成回答或保存质量基准。换库、身份、标签或关闭时取消请求并忽略迟到结果。

API 字段与校验见 [routes/knowledge.py](../../src/melonclaw/api/routes/knowledge.py)。主要入口：

| 操作 | 路径（库内路径以 `/api/knowledge-bases/{kb_id}` 为前缀） |
|---|---|
| 列表与建库 | GET / POST `/api/knowledge-bases` |
| 详情、编辑、删除 | GET / PATCH / DELETE 库路径 |
| 成员、共享与转让 | `/members`、`/members/{target_id}`、`/share-scope`、`/transfer` |
| 文档列表/上传/替换 | GET / POST `/documents`；替换显式指定文档，不按同名覆盖 |
| 重试、取消、删除 | POST `/documents/{id}/retry`、POST `/documents/{id}/cancel`、DELETE `/documents/{id}` |
| 原件与版本正文 | GET `/documents/{id}/source`、GET `/documents/{id}/text` |
| 搜索 | POST `/search` |

文档列表按分页读取本页文档最新作业，使用有界关联查询；打开、翻页、操作完成和手动刷新时读取状态。上传网络失败可用同一请求标识重放。格式、失败页与计费提示见导入章节。

## 部署与验证

先安装 [pgvector 扩展包](../../ops/PGVECTOR.md)，数据库角色无权限时由管理员预装扩展。新空库执行 `uv run melonclaw-db-init`。已有业务库执行 `uv run melonclaw-db-update`，由 `20261117_knowledge` 创建当前八张表和检索视图，不改已有业务数据。已有知识库表与当前结构不一致时升级失败并回滚，不重建、不删表。服务启动不自动建表或升级。

新库直接创建当前 schema。OCR 整文件处理和混合检索本身不增加表；当前库结构已就绪时，仅更新代码/依赖、构建前端并重启 Web。配置改变后重启生效，旧解析配方重试按前述规则拒绝。

日常运行沿用 `uv sync`、`uv run melonclaw-web` 与前端既有启动/构建命令；部署步骤见 [README](../../README.md) 和[部署说明](../deployment.md)。预期行为是文本导入不依赖 OCR Key，其他支持文件完成 OCR、分块、嵌入后原子发布；当前授权用户能在管理页检索，并在普通聊天中打开同版本引用。

全量检查执行 `scripts/check.sh`。无外部模型时可运行 `uv run pytest -q tests/test_knowledge_search.py tests/test_knowledge_query.py tests/test_knowledge_citations.py` 验证工程边界；真实 SQL 验证需独立 `MELONCLAW_TEST_DATABASE_URL`，缺少环境的跳过不能计为通过。

检索回执与管理页展示 vector/keyword/fused/sources 状态、候选/返回数、降级原因和耗时；日志只记录有界诊断数值，不记录查询、正文、身份或凭据。导入通过 job 的阶段、固定 error_code、安全摘要及有效版本指针观察，不记录供应商原文。

历史验证证据见[导入验收](../exec-plans/completed/knowledge-ingestion-acceptance.md)、[检索与聊天](../exec-plans/completed/knowledge-retrieval-and-chat.md)、[引用修复](../exec-plans/completed/2026-11-07-rwa-acceptance-fixes.md)、[权限与流程修复](../exec-plans/completed/2026-11-10-knowledge-review-fixes.md)及[阶段记录归档](../exec-plans/completed/2026-11-17-knowledge-design-history.md)。这些记录仅证明当时执行的范围，不代表当前外部服务已重新验收。

真实检索质量仍需使用业务语料约 30–50 个有证据位置的问题，对同一 query/top_k/文档范围比较 vector、keyword、hybrid，核对证据覆盖、revision/offset、延迟和嵌入调用次数。工程测试、RRF 分数和历史合成样本不能证明复杂中文研报质量或规模性能；当前未完成这组混合检索业务质量对照。自动物理清理、URL 导入和后台知识库查询也不能因存在设计记录而视为已实现。
