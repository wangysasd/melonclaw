# 会话与项目工作区

状态：已实现

## 背景与目标

普通对话需要脱离 Project 独立存在，同时保留刷新、重启和附件恢复能力；Project 内的多条
Conversation 需要共享项目文件。导航、文件归属和执行恢复必须使用同一套作用域，不能把“没有
Project”当成临时数据，也不能让浏览器提交的 ID 直接充当授权。

## 方案概览

- `chat_conversations.id` 是会话主键。表内没有重复的 `conversation_id` 列；其他表通过
  `conversation_id` 外键引用它。
- Conversation 的 `user_id` 必填，`project_id` 可空。`NULL` 表示普通会话，非空值必须是同一用户的 Project。
- 普通会话的工作区是 `workspace_root/conversations/<conversation_id>`；项目会话使用项目的受控
  `workdir_path`。因此普通会话 A/B 文件互不可见，同一 Project 的会话共享文件，不同 Project 独立。
- 全局“新建对话”创建普通会话；项目区域的“在此项目中新建”显式创建项目会话。已经停在
  无任何消息的空白会话上时，同作用域的新建请求直接复用当前会话，不再创建（跨作用域仍新建）。
  侧栏分为 Projects
  和 Recents，Recents 只列 `project_id IS NULL` 的会话。

## 数据与附件归属

`chat_attachments` 使用 `project_id` 和 `owner_conversation_id` 表示存储归属，数据库 CHECK
约束保证两者恰有一个非空。项目附件继续通过项目上传入口，普通会话使用
`POST /api/conversations/{conversation_id}/attachments`。元数据、内容、删除、解析重试、后台解析和
消息绑定都重新校验当前用户及同一作用域；普通会话 A 的附件不能绑定到 B，项目附件不能绑定到
另一个项目。上传幂等键按作用域隔离，配额和锁也按项目/会话分别计算。

附件能力接口返回 `workspace_max_bytes`。配置变量 `MELONCLAW_ATTACHMENT_PROJECT_MAX_MB` 沿用
现有名称，但数值语义是每个工作区的原文加派生文件配额。

## Agent、恢复与缓存

运行时统一通过 `agent_for_conversation()` 解析 Agent 和工作区。缓存键包含工作区、模型快照和
客户端能力；`MELONCLAW_AGENT_CACHE_ENTRIES` 限制可重建实例数量。消息执行、审批恢复、用户问题
恢复、历史 pending 查询和 TTL 收尾都从会话记录解析 Project，不能使用当前侧栏选中的 Project
替代。Checkpoint 仍以会话 ID 作为 `thread_id`，工作区共享不代表消息状态共享。

## 失败与安全边界

服务层始终按 `user_id` 重新校验用户、Project、Conversation 和附件归属；UUID 难猜不是授权。
路径解析必须位于 `workspace_root` 内，`LocalShellBackend` 仍不是安全沙箱。写文件、删除和 Shell
继续遵守既有 HITL/权限边界。数据库结构只在 `database/schema.py` 维护，开发环境允许清空后执行
`uv run melonclaw-db-init` 重建，不写历史迁移或兼容分支。

## 实现位置与验证

核心实现位于 `database/schema.py`、`repository/conversations.py`、`repository/attachments.py`、
`services/runtime.py`、`services/attachments.py`、`services/execution.py`、
`api/routes/attachments.py` 和 `frontend/src/state/session.tsx`。已验证：

- `uv run pytest -q tests`：77 passed
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run lint`

目标开发 PostgreSQL 已完成清空重建，且项目/普通会话列表均为空；事务并发配额、跨用户/跨
会话拒绝和浏览器手工验收仍需执行。
