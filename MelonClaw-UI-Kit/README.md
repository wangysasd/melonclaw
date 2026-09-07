# MelonClaw UI Kit

此资源包服务于现有原生 HTML/CSS/JavaScript 前端的 UI 改进。未修改 GitHub 仓库，未提供已运行的新 UI。

## 使用

1. 将资源包解压到你的工作区，让 Codex 能访问这些文件。
2. 复制 MelonClaw-UI-Prompt.md 全文交给项目中的 Codex。
3. Codex 按 Prompt 将需要的资源复制到 src/melonclaw/web/static/assets/，完成接入、优化和验收。

没有资源包也可执行 Prompt 中的布局修改；图标可暂时沿用现有资源。

## 品牌图

assets/melonclaw-mark-white.png 是根据仓库 melon-claw.png 用内置图像生成工具编辑出的无文字品牌提案。保留三个棕色瓜子和瓜爪西瓜掌垫，没有新增果肉内瓜子。

这是 1254×1254、RGB、近白背景的 PNG 源图，**不是透明图片**。适合放在白色图标容器内；在浅绿或深色底上直接铺图会看见白底。可用于侧栏 36–40px、助手头像 32px、欢迎区 64–72px。实施时应导出小尺寸资源并检查 favicon 的可辨识性，不能仅以 CSS 缩放大图当作性能优化。当前包中保留高分辨率源图，没有宣称它已经是压缩后的生产资源。

本轮曾生成一个带棋盘格的版本，其 RGB 像素并非真正透明，已排除出交付包。只采用当前白底版本。

## 功能图标映射

| 场景 | SVG 文件 |
| --- | --- |
| 新建对话/项目 | plus.svg |
| 对话 | message-circle.svg |
| 项目 | folder.svg |
| 发送 | arrow-up.svg |
| 下拉/展开 | chevron-down.svg、chevron-right.svg |
| 手机菜单/关闭 | menu.svg、x.svg |
| 查找资料 | search.svg |
| 整理思路 | list-checks.svg |
| 制定计划 | calendar-days.svg |
| 已完成/失败/处理中 | circle-check.svg、circle-alert.svg、loader-circle.svg |
| 人工确认 | shield-check.svg |
| 复制 | copy.svg |
| 外部来源 | external-link.svg |
| 文件内容 | file-text.svg |

SVG 为官方源文件，24×24 viewBox，默认 2px 描边。显示建议 18–20px；可以通过引用原文件的 CSS mask 统一着色，或者接入可信图标库。外部 img 引用的 SVG 不会自动继承父节点的 currentColor。此处未使用手绘近似图形替代官方图标。

图标来源：https://github.com/lucide-icons/lucide/tree/3859eb20fabe7fd95652fcd4395843b6c0bcdd01/icons
图标来源提交：3859eb20fabe7fd95652fcd4395843b6c0bcdd01
版权/许可：见 assets/icons/LICENSE.txt，包含 Lucide ISC 与相关 Feather MIT 声明。图标来自开源项目，并非本轮原创设计。

## 代码审阅边界

参考 MelonClaw 提交：dee268d5da1866750f1f0f302fcd89bd57a4ecb4。
当前浏览器访问策略阻止预览，因此本包中的建议是源码和图片审阅，未取得页面截图。Prompt 已要求实施阶段验证桌面、手机、首条消息、Markdown、SSE、用户切换和审批。
