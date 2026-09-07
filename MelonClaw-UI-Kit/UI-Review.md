# MelonClaw 前端 UI 修改建议

审阅日期：2026-09-07。代码依据：[wangysasd/melonclaw，dee268d](https://github.com/wangysasd/melonclaw/tree/dee268d5da1866750f1f0f302fcd89bd57a4ecb4)。

这份意见来自当前前端源码与仓库原始 PNG 图片。已尝试以原始前端和模拟接口预览，浏览器访问策略阻止了本地页面访问；没有获得可用页面截图。因此这不是完成了浏览器流程验收的视觉审查。页面实际排版、溢出、移动端键盘与无障碍表现仍需由实施阶段验证。

## 设计结论

推荐“奶白工作台 + 瓜皮绿操作 + 少量果肉红 + 瓜子爪印”。保留两栏聊天结构，将品牌延伸到 Logo、助手头像、欢迎空状态、选中态和 favicon。大幅背景插画对当前聊天界面价值有限，先补适用于小尺寸的无字标志与统一功能图标。

现有优点包括独立的会话滚动、桌面底部输入区、工具完成后收起、子 Agent 层级、审批组件和读屏状态区；应保留这些基础。

## 根据代码确认的问题与建议

| 优先级 | 区域与代码依据 | 发现 | 建议 |
| --- | --- | --- | --- |
| P1 | app.js：loadConversations、clearConversationView、updateComposer | 初始化会清除静态欢迎区，无会话时禁用输入 | 统一欢迎/空状态渲染，允许在上下文就绪后先输入，首次发送再保证会话存在 |
| P1 | index.html；styles.css：brand-mark、avatar；三张原图 | 带字 Logo 缩为 34/28px，旁边又重复产品名；melon.png 实际是人物头像 | 无字标志与文字分离，统一 MelonClaw，用户头像与品牌头像分开 |
| P1 | styles.css：project-name、conversation-title、conversation-time | 标题仅 11px，必要元信息 9px；#9AA6B5 在白底的对比度约 2.47:1 | 标题 13–14px、元信息 12px，更深的中性绿灰 |
| P1 | app.js：renderHistory、handleEvent | 回答全部使用 textContent，Markdown 标记直接显示 | 安全 Markdown 渲染，正文 15–16px，代码/表格局部滚动 |
| P1 | styles.css：max-width:620px | 小屏改为侧栏上、聊天下，主面板另占 100dvh | 手机抽屉侧栏，聊天占主视口；这是结构性风险，尚无设备截图验证 |
| P2 | 侧栏 DOM 与 renderConversationPanel | 会话区在整个项目列表后，单靠缩进容易使归属不清 | 加当前项目会话标题；把模拟用户和运行详情移到底部 |
| P2 | index.html 的 LIVE；refreshStatus | LIVE 是固定标签；就绪状态下可能直接写“网页查询已连接” | 状态由真实返回字段和已有事件驱动，无数据时不宣称连通 |
| P2 | tool-head、approval-panel、approval-slot | 工具信息 9–10px；多项审批区没有明确高度约束 | 可读摘要、原始详情折叠；审批列表内部滚动 |
| P2 | window.prompt；输入框 keydown；addMessage | 新项目用浏览器原生弹窗；Enter 无组合输入判断；历史用户时间均写“刚刚” | 主题 dialog、中文输入法保护、真实时间或不显示 |
| P2 | 静态资源 | 使用的两张大 PNG 共约 2.76MB，却展示为小头像/图标 | 交付适配显示尺寸的资源，保留原图，按需加载 |

## 配色建议

| 用途 | 值 |
| --- | --- |
| 画布 | #F8FAF6 |
| 卡片/阅读面 | #FFFFFF |
| 侧栏 | #F2F6F0 |
| 正文 | #20392E |
| 辅助文字 | #607266 |
| 主操作/链接 | #176B4A |
| 选中背景 | #E8F4EA |
| 品牌点缀 | #F56B70 |
| 瓜子 | #403329 |

建议主操作绿与白色的对比度约 6.48:1，辅助字在画布上约 4.88:1。珊瑚红配白字只有约 2.91:1，因此它适合作图形点缀，不适合普通小字号按钮白字。这些是色值计算，不能替代实际页面的完整无障碍验收。

## 素材与交付

- MelonClaw-UI-Prompt.md：完整可复制实施指令，包含文件范围、状态逻辑、视觉规格与验收要求。
- assets/melonclaw-mark-white.png：保留原品牌意象的无字白底提案，1254×1254 RGB，非透明素材。
- assets/icons/：18 个 Lucide 原版 SVG，保留许可证。
- melonclaw-theme-tokens.css：供实施时合并的建议 tokens，不是可直接覆盖现有布局的成品样式。
- README.md：素材用途、接入路径与来源。
- ASSET-GENERATION-PROMPT.md：品牌图标的生成约束和方法记录。

## 主要源码依据

- [index.html](https://github.com/wangysasd/melonclaw/blob/dee268d5da1866750f1f0f302fcd89bd57a4ecb4/src/melonclaw/web/static/index.html)
- [styles.css](https://github.com/wangysasd/melonclaw/blob/dee268d5da1866750f1f0f302fcd89bd57a4ecb4/src/melonclaw/web/static/styles.css)
- [app.js](https://github.com/wangysasd/melonclaw/blob/dee268d5da1866750f1f0f302fcd89bd57a4ecb4/src/melonclaw/web/static/app.js)
- [原始品牌图](https://github.com/wangysasd/melonclaw/blob/dee268d5da1866750f1f0f302fcd89bd57a4ecb4/src/melonclaw/web/static/melon-claw.png)

## 技术参考

[Lucide 原生 JavaScript 文档](https://lucide.dev/guide/lucide)说明了无需框架的图标接入方式。[Marked 文档](https://marked.js.org/#usage)明确其输出不自带 HTML 清洗；可以结合[DOMPurify](https://github.com/cure53/DOMPurify)处理模型生成的 Markdown。
