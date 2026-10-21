# 清透磨砂聊天工作区

状态：已实现（2026-10-19）

## 背景与目标

在用户确认的一体化圆角聊天面板基础上，引入彩色磨砂玻璃素材。图标增加入口辨识度，正文保留白底与 14px 阅读体验。原品牌 Logo、供应商 Logo、聊天协议与权限不变。

## 方案与关键选择

六个透明底图标与一张侧栏装饰通过内置 image_gen 工具生成，部署版本为 `frontend/public/assets/glass/` 中的本地 WebP；不在运行时调用生成服务或加载外站。原始生成图不随应用部署，发布素材保留 alpha。图标统一为接近正面的轻俯视角、左上光源、彩色透明后层与乳白磨砂前层。

| 素材 | 位置 | 配色 |
|---|---|---|
| home | 全局首页入口 | 冰蓝 |
| clock | 定时任务入口 | 薄荷绿 |
| layers | 拓展入口、能力与行业卡片 | 蓝紫 |
| chart | 估值、预测、行情与曲线卡片 | 冰蓝 |
| document | 研报、财务、日报与信用卡片 | 薄荷绿 |
| globe | 宏观、境外与搜索卡片 | 蓝紫 |
| sidebar | 会话侧栏底部低对比装饰 | 蓝绿 |

聊天面板外侧、会话侧栏与全局导航共用不透明 `workspace-canvas` 浅蓝底色（#EFF6FD），避免半透明背景叠加产生灰色接缝；白色聊天面板保持独立。装饰图通过背景遮罩弱化，避免影响长会话列表。渐变、高光与阴影由 `theme/tokens.ts` 统一维护。控件及卡片沿用既有圆角；导航选中态使用白色表面和 `aria-current`，不靠图标颜色表达状态。导航图标 32px、卡片图标 40px，布局预留尺寸；不替换工具状态等小尺寸 SVG。

## 数据与事件流

静态素材经 GlassIcon 渲染至既有按钮。无新增网络业务请求、持久化、权限或审批入口。权益/固收各 12 张卡片、轮换暂停、键盘切换以及点击填入草稿均沿用现有逻辑；图片是装饰，使用空 alt，名称由按钮文字或无障碍标签提供。浏览器加载失败时文字与操作仍可使用。

## 运行步骤与预期结果

沿用 `cd frontend && npm run dev`；部署时执行前端生产构建。无需建库、升级、模型或 MCP 凭据。欢迎页和左侧栏出现玻璃材质，历史消息不增加装饰，圆角容器、隐藏滚动条及文件面板保留。

## 素材生成提示词

使用内置 image_gen，未使用 CLI/API fallback。每张图独立生成。图标公共提示词：

> Single production UI icon, transparent alpha background. Clear colorful frosted glass, saturated translucent rear layer, milky translucent front face, fine white rim highlight, gentle internal refraction, soft ambient shadow only beneath object. Almost front view with slight elevated three-quarter depth, upper-left lighting. Centered square canvas, equal safe padding, crisp simple silhouette readable at 28px. No background tile, lettering, watermark, extra objects, sparkle, ground plane or excessive blur.

对象分别为房屋、双铃闹钟、叠放圆角方块、递增柱状图、折角文档、经纬线地球；对应色彩见上表。

侧栏提示词：

> Subtle decorative sidebar-bottom illustration: three overlapping curved frosted glass fins / translucent ribbons in icy blue, pale cyan and mint, smooth sculptural glass, upper-left light, delicate milky surfaces. Wide 3:2 composition, transparent alpha background, visual weight in lower half, upper edges softly dissipate. No text, logo, icons, sparkle, frame, rectangular background or ground plane.

## 验证记录

七张 WebP 均核验 RGBA 透明通道；六张图标为 160×160，侧栏装饰为 780×520，总体积 80,174 字节（约 78KiB）。浏览器核验 375 / 768 / 1024 / 1440px 无页面水平溢出，所有玻璃图标加载成功，Header 分割线为 0px。权益/固收各 12 张卡片，点击能力卡片只填草稿，进入拓展再返回聊天保留草稿。既有测试覆盖发送、审批及流式行为；未调用真实模型或 MCP。

`scripts/check.sh` 全部通过；最终前端生产构建通过，保留既有分块体积提示。截图保存于本地 `output/playwright/glass-workspace/`。


## 内外材质平衡

按用户反馈进一步减淡侧栏底部装饰。聊天容器顶部冰蓝柔光在 240px 内淡出为纯白，Header 使用透明背景继承同一渐变，不绘制分割线；消息滚动不改变渐变位置。输入框使用淡蓝白渐变、细高光边和稳定的浅阴影，项目栏改用同系浅蓝。正文阅读区保留白色，输入聚焦不增加额外光晕，不移动或改变几何尺寸。材质统一通过 chat-surface、composer-surface、sidebar-art-veil 与 shadow-composer 维护。

内外平衡验收：375 / 768 / 1024 / 1440px 无页面水平溢出，Header 透明且分割线为 0px，输入框聚焦前后背景、边框与阴影一致。桌面/手机截图为本地 balanced 系列。后端检查、前端 lint/类型检查及生产构建通过。默认并发全量前端测试两次分别出现账号表单和任务操作用例的 5 秒超时；未修改测试或超时阈值，降低为最多 2 个 worker 后全套 50 文件、380 用例通过。


## 消息配色

用户气泡使用 `message-user-bg`（#E4EFFF）、`message-user-ink`（#243B53），无边框和阴影；`message-user-border`（#D5E4F5）仅用于行内代码底色和引用边缘。保留方向圆角、14px 字号及现有消息布局；用户正文与行内代码使用同一深色，链接使用更深的品牌蓝。助手正文保持直接呈现在阅读区，不增加彩色气泡。引用块及已有工具参数/结果详情底色使用 `message-detail-bg`（#F1F5FA），引用文字为 `message-detail-ink`（#526477），执行摘要保持透明。无聊天行为、模型请求或持久化变化。

气泡验收：375 / 768 / 1024 / 1440px 实测用户气泡为 #E4EFFF 底、#243B53 正文、#D5E4F5 边框和 14px 字号，无页面横向溢出。工具详情展开后底色为 #F1F5FA。用户正文、引用、用户链接及行内代码对比度分别约 9.91 / 5.56 / 5.72 / 8.90，均超过普通文字 4.5:1。`scripts/check.sh` 全部通过；生产构建通过（保留既有分块体积提示）。截图为本地 `output/playwright/glass-workspace/bubbles-1440.png`；未调用模型或 MCP。


## 文件抽屉一致性

打开文件面板后，布局容器仅负责并排/覆盖定位；聊天与文件面板分别保留完整 16px 圆角、细外边缘，桌面中间留 4px 浅蓝背景。原左侧硬竖线、Header 与文件工具栏下边线移除。抽屉 Header、文件列表与预览区继承同一顶部柔光，内容裁切在四角内。拖动区域默认透明、悬停/键盘聚焦提示，放大和窄屏隐藏；滚动条沿用按需显示的浅色风格。文件内容本身不重写，HTML/PDF 预览保留原文件的表面。

布局仅修改 artifacts.css，未改文件接口、权限、焦点与键盘行为。关闭、放大/恢复、拖拽调宽及预览行为沿用既有链路，无需数据库变更。

文件抽屉验收：375 / 768 / 1024 / 1440px 均无页面水平溢出，四角圆角为 16px，Header 下边框为 0px；鼠标与键盘调宽、放大/恢复、Escape 关闭实测通过。截图为本地 file-surface 系列。后端、前端 lint/类型检查通过；默认并发前端测试出现既有账号表单 5 秒超时，未改测试或阈值，降低为最多 2 个 worker 后全套 50 文件/380 用例通过（含文件浏览和预览测试）。生产构建通过，保留既有分块体积提示。

文件范围切换仅在 `.file-scope-segmented` 内采用浅蓝分组底、白色选中块、蓝色选中文字，无默认灰色阴影；悬停使用浅蓝提示，字号仍为 14px，窄屏保留 44px 点击高度。其他 Segmented 不受影响。

文件抽屉顶部“文件 / 当前文档”标签统一采用浅蓝分组底、白色选中块与蓝色选中文字，未选中为次要文字色，悬停为浅蓝提示；分组按内容收缩，长文档名仍省略，关闭及窄屏触控规则保留。

聊天面板外边框移除，单独显示与文件抽屉并排时均以圆角、表面色及外部留白区分区域；文件抽屉及输入控件自身边框不受此调整影响。


## 透明品牌素材

侧栏与欢迎页的默认字标改用 `frontend/public/assets/brand/melonclaw-word-transparent.webp`，消息头像改用 `melonclaw-mark-transparent.webp`。使用内置 image_gen 图片编辑模式分别移除原灰白底，保留现有字形、瓜爪轮廓和配色；发布素材为 RGBA WebP。头像容器同时去掉实色背景与边框，避免透明图外仍有方块。RMS 原图片不替换，默认登录页仍沿用原素材；无新增外站请求或运行时生成服务。

提示词：Use case: background-extraction. Remove only the off-white/light gray background completely. Preserve the exact MelonClaw heavy rounded black wordmark, watermelon replacing the o, letter shapes, spacing and colors; for the mark preserve three dark brown teardrop seeds above the red watermelon pad, pale green rind and dark green base. True transparent alpha background including gaps and lettering counters, smooth antialiased edges. No white matte, rectangle, checkerboard, ground shadow, new text or objects.

透明素材验收：两张发布 WebP 均核验包含从 0 开始的 alpha 通道，字标 1086×362、头像 192×192；浏览器确认新图片成功加载、助手头像容器背景透明且边框 0px，四档宽度无页面水平溢出。scripts/check.sh 全部通过；生产构建通过（保留既有分块体积提示）。实际截图位于本地 `output/playwright/glass-workspace/transparent-brand-1440.png`。


## 会话侧栏与聊天统一外轮廓（2026-10-21）

侧栏现纳入 home-workspace 的统一 16px 圆角面板，采用纯浅蓝灰底，移除侧栏装饰纹理与磨砂；聊天内部接缝平直。外层统一承担留白及视口高度，折叠、窄屏抽屉和文件覆盖保持原行为。此约定取代前文侧栏位于聊天面板外的说明；具体布局见 [视觉一致性](ui-consistency.md)。
