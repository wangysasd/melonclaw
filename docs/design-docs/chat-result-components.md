# 聊天结果组件

状态：已实现（第一版）
日期：2026-10-02

## 背景与目标

在现有聊天链路内，让输出成为可查看、可核对、可下载的结果。普通文字继续使用 Markdown；图片、文件、差异、数据图表和来源使用固定组件。没有引入 A2UI，也不运行模型输出的 HTML、JavaScript 或任意 ECharts 配置。

## 方案与数据流

1. `core/prompts.py` 的 `RESULT_OUTPUT_GUIDANCE` 告诉模型输出显式 `melon-result` JSON 围栏，或普通 Markdown 表格／Mermaid／受控图片引用。
2. 原始正文随既有 assistant steps、完整终态快照和历史记录保存；没有新增数据库表、字段或 SSE 事件。前端只在围栏完成后渲染结果。格式失败保留可读原文。
3. `resultBlocks.ts` 校验版本、类型、字段白名单、大小、行宽、有限数值和来源 URL。`ResultBlockView` 选择固定组件。绘图和数据表共用 `rows`，缺失值使用 null、不填零；CSV 导出空单元格。
4. 附件引用 `attachment_id`，沿用附件权限 API；服务端 hydration 在模型请求副本中提供真实 ID，不把图片 base64 写入图状态。
5. 成果文件只能引用 `/outputs/` 的虚拟路径。`ResultFileService` 解析有效用户、校验会话和所属项目后使用现有工作区定位；`storage/results.py` 用目录 fd 和 `O_NOFOLLOW` 逐层打开，拒绝隐藏路径、相对跳转、符号链接和非普通文件。
6. 文件名、类型、大小来自服务器。下载时再次核验，以受控流读取同一个文件描述符，避免检查后替换路径。前端用 Blob 下载，失败可见；预览只允许核验的图片、小型 UTF-8 文本和 PDF。

## 统一结果格式

每个 `melon-result` 围栏一个 JSON 对象，`version: 1`。未知类型／未知字段／无效引用都回退原文。格式限制服务于安全与可用性，不是历史兼容分支。

| type | 数据字段 | 展示 |
|---|---|---|
| image / file | ref、可选 caption | 缩略图／文件名、类型、大小、预览、下载 |
| chart | title、chart、unit、x_label、series、rows、sources、可选 note | 折线／柱状图、来源与单位、默认折叠绘图表、CSV |
| table | title、columns、rows | 表格、复制 TSV、下载 CSV、横向滚动 |
| diff | file_name、before、after | 行级新增／删除高亮；长内容折叠 |
| sources | items | 标题、链接、引用片段、位置与附件／成果引用 |

ref 只接受 `{"attachment_id":"实际 UUID"}` 或 `{"path":"/outputs/实际文件"}`，二者互斥。不能接受模型提供的任意下载 URL。

来源项要求 `id` 与 `title`；可选 `url`（HTTP(S)、无凭据）、`quote`、`locator` 和 `ref`。正文 `[1](#source-1)` 定位并展开同一段 Markdown 的来源块，页面生成独立锚点，避免不同消息／执行过程串号。每段回答最多一个独立 sources 块。图表自己的 sources 列表不参与正文编号定位。

```melon-result
{"version":1,"type":"chart","title":"月度销售额（示例数据）","chart":"line","unit":"万元","x_label":"月份","series":["销售额"],"rows":[["一月",12],["二月",18]],"sources":[{"id":"1","title":"示例数据"}],"note":"模拟数据，仅用于演示。"}
```

最大结果块 200000 字符；图表 200 行、8 系列；结构化表格 1000 行、30 列；来源列表 30 项；差异两段合计 120000 字符。更大数据交付文件。行差异用有上限的 LCS，超过计算上限显示明确标注的整段替换预览。审批 `edit_file` 复用差异组件，只展示脱敏参数，不执行修改。

## Markdown 与交互

- Markdown 图片支持 `/attachments/实际UUID`、`/outputs/plot.png` 引用；先取得受控元信息再加载。HTTP(S)、data URL、任意 `/api` 或其它图片地址不会自动加载，不代理外部图片。
- 现有 Markdown 表格获得复制和 CSV 下载；CSV 文本字段防电子表格公式执行、正确转义逗号／换行／引号，UTF-8 BOM 兼容中文。真正的数值负数保持数值。复制使用 TSV。
- Mermaid 保持严格模式与源码校验，增加图形／源码切换、放大和 SVG 导出；失败保留源码。导出只序列化已由库生成的 SVG。
- 图表使用 ECharts 按需导入折线／柱状图、SVG renderer、坐标轴、图例、tooltip、无障碍描述，组件懒加载。tooltip 使用图形文本、不使用 HTML。运行失败只影响图形，来源和数据表保留。
- 正文／辅助文案 14px，操作按钮桌面 32px 高、8px 圆角，窄屏至少 44px。宽表和差异在自己的容器滚动。

## API 与权限

新增只读接口：

- `GET /api/conversations/{conversation_id}/result-files?user_id=...&path=/outputs/...`：返回 file_name、size_bytes、media_type、preview_kind。
- `GET /api/conversations/{conversation_id}/result-files/content?user_id=...&path=/outputs/...`：默认 attachment 下载；`preview=true` 仅在支持的类型上内联。

重新解析用户有效租户归属、重新校验会话／项目，不相信模型的身份或宿主机路径。内容响应带 nosniff、sandbox CSP、no-store。PNG/JPEG/WebP/GIF 检查实际内容和像素限制；PDF 检查标识，浏览器预览放在 sandbox iframe；文本预览上限 200000 字节，React 纯文本渲染。其它类型（含 HTML、SVG、Office）只下载。成果读取受现有单附件大小限制控制，不新增环境变量。

没有新增 Agent 工具，没有新增副作用；文件生成仍走既有 HITL 工具，不加入 Interpreter/PTC。读取 HTTP 接口无需 HITL，因只读且先校验归属。

## 失败与当前边界

- 文件未创建、被删除、权限失效、图片失效／加载失败，显示原因和重试。格式错误／未闭合结果保留原文。
- 来源在界面明确为回答提供的信息。服务端核验附件／成果归属，不独立验证外部网页或数字真实性。模型必须遵守真实来源和模拟数据标注规则；第一版没有来源抓取或独立证据登记工具。
- 原始聊天文本保存完整图表数据，可刷新恢复；成果指向当前工作区文件，后续覆盖会改变历史卡片，不是不可变版本。
- 普通会话移动到项目后沿用移动后的工作区；项目内同一用户的会话共享项目成果。仅交付 `/outputs/` 的文件，不提供全部工作区浏览。
- LocalShellBackend 仍非安全沙箱。HTTP 路径约束不防已获 Shell 权限的用户操作宿主机或硬链接；当前只适合本机开发，不是共享环境隔离方案。
- 大表／大图应交付文件；不支持饼图、双轴、自定义 option、外部图片自动加载。

## 运行与验证

```bash
npm --prefix frontend install
uv run melonclaw-web
npm --prefix frontend run dev
scripts/check.sh
npm --prefix frontend run build
```

依赖新增 ECharts，锁文件同步；无数据库结构变化，无需为本次改动重新建表。已启动服务需重启后端，让下一轮 Agent 使用新提示词。

验证记录：

- `scripts/check.sh` 全部通过：后端编译、测试（含架构与文档链接）、ruff、锁文件，前端 lint、类型检查、测试。
- `npm --prefix frontend run build` 通过；数据图表独立懒加载 chunk，构建有现有大 chunk 提示，不阻断构建。
- `tests/test_result_files.py` 覆盖真实身份／项目服务的跨作用域拒绝、路径穿越／隐藏路径／符号链接／非普通文件拒绝、真实图片校验、HTML 仅下载和安全 HTTP 响应。`test_result_hydration.py` 验证真实附件 ID 出站索引且不修改持久消息。
- 前端结果组件测试覆盖真实 Markdown 接入、未闭合流式围栏、格式失败与危险字段拒绝、图表／数据表一致性、公式文本／CSV 转义、编号来源定位、图片失败重试、真实元信息展示、纯文本预览与下载失败、长差异折叠。
- 桌面与 390px 窄屏使用实际组件、ECharts／Mermaid，以及实际 ResultFileService 和 HTTP 读取路由，数据与归属仓储使用本地固定夹具。两种图表、图片、文件预览正常；窄屏整页无横向溢出，表格自己滚动、按钮 44px。截获下载 Blob 核对绘图 CSV 数值及单位，Mermaid SVG 导出成功，文本预览显示实际文件正文。
- 未连接真实数据库、模型或外部 MCP 进行端到端验证；不能据此声称模型稳定遵守格式或来源事实已经独立核验。
