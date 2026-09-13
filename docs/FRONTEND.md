# MelonClaw 前端

前端位于 `frontend/`，是独立的 React + Vite + TypeScript 项目，通过 HTTP 和 SSE 与后端通信。

## 1. 技术栈与命令

- React 18 + Vite 5 + TypeScript 5.6
- UI：`antd` 6 + `@ant-design/x`（Sender、Bubble 等会话组件）+ `@ant-design/x-markdown`
- 图表渲染：`mermaid`
- 测试：`vitest` + `@testing-library/react`

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1   # 开发模式，Vite 把 /api 代理到后端
npm run lint                      # eslint src
npm run typecheck                 # tsc --noEmit
npm test                          # vitest run
npm run build                     # tsc --noEmit && vite build，产物在 frontend/dist/
```

## 2. 目录职责

| 目录 | 职责 |
|---|---|
| `src/api/` | HTTP 与 SSE 客户端：`client.ts`（JSON 请求封装与错误归一化）、`stream.ts`（fetch + ReadableStream 手动解析 SSE 帧） |
| `src/state/` | 会话级全局状态（`session.tsx`）：用户、租户、Project、Conversation、当前流、`epoch` 失效机制 |
| `src/hooks/` | 流式消息 reducer 与副作用：`useChatStream.ts` 处理 optimistic 消息、增量文本、审批、断线重同步 |
| `src/components/` | 展示与交互组件（Composer、ChatView、侧栏、审批卡、技能选择等） |
| `src/types/` | 与后端契约对应的类型：`api.ts` 里的 `Message`、`SendMessageInput`、`StreamEvent` 判别联合 |
| `src/styles/` | 分文件维护的样式：`global.css`、`sidebar.css`、`chat.css` |
| `src/theme/` | antd 主题配置 |
| `src/lib/` | 与框架无关的纯函数工具 |
| `tests/` | vitest 用例；`setup.ts` 是公共初始化 |

## 3. 与后端的契约

- 请求：`POST /api/conversations/{conversation_id}/messages`，JSON 请求体，字段见 `src/types/api.ts` 的 `SendMessageInput`。
- 响应：`text/event-stream`，由 `src/api/stream.ts` 手动分帧解析（按空行切帧，逐帧解析 `data:` 行）。
- 事件类型以 `src/types/api.ts` 的 `StreamEvent` 判别联合为准（`message_started`、`text`、`tool_call`、`approval_required`、`completed` 等）。**新增事件类型必须同时改后端 `output/events.py`、`api/sse.py` 和前端这个联合类型**，否则前端会静默丢弃。
- 断线重连：不依赖 SSE 缓存，而是靠会话重新同步；`session.tsx` 的 `epoch` 用来丢弃切换会话后迟到的响应。

## 4. UI 约定

以下约定是长期维护的视觉与交互基线，改动前请先确认是否会影响其它页面。

### 侧栏与顶栏

- 侧栏左上角保留瓜爪图标，右侧使用 `frontend/public/assets/brand/melonclaw-word.png` 字标图片，不再单独显示「MelonClaw / 瓜爪助手」两行文字。
- 欢迎页只保留主标题和快捷提示卡，不显示额外的品牌问候语与说明文字。
- 会话侧栏首次显示最近 10 条记录，通过「加载更多会话」继续分页；滚动条默认隐藏，悬浮或键盘聚焦滚动区域时显示。
- 模拟用户下拉项只显示用户名，字号 14px，不显示租户数量。
- 顶栏只显示靠左的当前会话名称：字号 16px、不加粗、背景色 `#F5F5F7`，高度相较原样式降低 20%。
- 模型选择器的下拉选项与选中后展示的模型名称统一为 14px。

### 消息区

- 思路摘要、工具活动标题、文字正文和代码块使用一致的阅读宽度。
- 摘要阶段数据和工具活动标题使用 14px 常规字重。
- 代码块使用系统蓝色标题和边框，代码正文为 14px 等宽字体，并与聊天正文保持一致的相对行高。

### 设计语言

整体采用 Apple 风格色板：背景 `#F5F5F7`、主文字 `#1D1D1F`、强调色 `#0071E3`；侧栏与顶栏为磨砂效果（backdrop blur）；用户气泡使用 iMessage 蓝；代码块为深色 `#1D1D1F`。

## 5. 行为约定

- 输入区支持 Enter 发送、Shift + Enter 换行；输入 `/` 打开当前 Project 的 Skill 目录，可按 Skill ID、名称、描述过滤，Escape 关闭。
- **Skill 目录**：目录摘要由只读 `GET /api/skills` 提供；选中的 Skill 通过消息请求的 `skill_id` 传给后端，后端会重新校验并让 Agent 按对应的 `SKILL.md` 执行；未选择 Skill 时保持自动发现逻辑。
- 回复生成中或等待审批时仍可编辑下一条草稿，但发送按钮锁定。
- 模型选择从**下一条消息**生效；正在执行的回复和审批恢复仍使用原运行的模型。
- 模型输出期间仍可「新建对话」（⌘/Ctrl + K），原会话的流连接保持不中断；返回原会话可查看结果或处理审批。
- 助手回复会隐藏 `<think>…</think>` 推理块和内部工具选择 JSON。过滤只作用于展示，运行状态保留原始消息。
- **Markdown 渲染**：使用 Ant Design X Markdown，支持嵌套列表、表格、代码复制、公式和安全 Mermaid 图表。
- **执行摘要**：工具与子 Agent 活动在可展开的摘要面板中显示；失败、等待审批、未收到结果会分别标注。

## 6. 部署

生产部署时由 Nginx 或 Node 静态服务托管 `dist/`，并将 `/api` 反向代理到 FastAPI（**SSE 必须关闭缓冲**）。跨域直连时用 `VITE_API_BASE_URL` 指定后端地址，后端侧用 `MELONCLAW_ALLOWED_ORIGINS` 放行 origin。
