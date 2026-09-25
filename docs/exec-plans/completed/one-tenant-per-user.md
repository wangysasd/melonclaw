# 用户唯一租户归属改造

状态：已完成  
开始日期：2026-09-25  
完成日期：2026-09-25

## 目标

每个用户始终归属一个租户。所有请求只提交用户身份，服务端从用户记录取得唯一的租户、角色和状态；Agent 运行、Memory 和审计继续使用这份经过数据库校验的上下文。验收时不存在“未指定租户就选 ID 最小者”的路径，也不存在客户端独立选择租户的状态。

## 背景

改造前 `users` 与 `tenants` 通过 `user_tenants` 构成多对多关系。`get_user_context(user_id, tenant_id=None)` 在缺少 `tenant_id` 时按租户 ID 升序取第一条有效关系；前端也从 `tenant_ids` 中选默认租户并把 `tenantId` 传给多数 API。Project、Conversation 和附件实际上按 `user_id` 归属，租户主要用于成员资格、Tenant Memory 和记忆权限。这让同一用户的会话运行上下文可以随请求变化。

本方案采用开发期可清空重建的前提，直接收敛数据模型和前后端协议。数据库与本地工作区的旧数据可删除；不编写迁移、旧字段读取、旧 API 参数兼容或本地存储迁移。

## 目标模型与规则

| 对象 | 目标结构与约束 |
|---|---|
| `tenants` | 保留租户 ID、名称和创建时间；一个租户可有多个用户。 |
| `users` | 保留全局唯一的 `user_id`；新增非空 `tenant_id` 外键、`tenant_role` 和 `tenant_status`。每行天然只有一个租户。`tenant_status=active` 才能进入用户请求上下文。 |
| `user_tenants` | 从当前业务 Schema 和预期表清单删除。角色与状态归入 `users`，不维护第二份成员关系。 |
| Project / Conversation / Attachment | 继续以 `user_id` 校验归属；它们的租户可由所属用户唯一确定。普通会话与项目会话的工作区规则、Conversation ID 和 Checkpoint `thread_id` 继续沿用。 |
| `UserContext` / `AgentContext` | 保留服务端解析出的 `tenant_id`、租户角色等运行字段；这些值只来自数据库，不来自请求或浏览器状态。 |
| Memory / `memory_events` | Global、Tenant、User 三种 namespace 不变；Tenant namespace 和事件审计继续记录数据库解析的 `tenant_id`。 |

用户的租户归属在运行中的应用里视为固定属性；目前没有更改用户租户的业务入口。开发数据要更换归属时重建数据库，避免旧会话、Checkpoint 和工作区在不同租户记忆上下文中继续使用。

## 请求与运行数据流

```text
客户端提交 user_id（当前仍是开发模拟身份）
  → API 读取 user_id（附件路由使用 api/identity.py）
  → repository 读取 users → tenants，要求用户状态为 active
  → services 用 user_id 校验 Project / Conversation / Attachment 归属
  → AgentContext 使用该用户唯一的 tenant_id 与角色
  → Memory 按 tenant_id 选 namespace、校验角色并记录审计事件
```

审批恢复、用户问题恢复、取消与过期收尾都重新读取同一用户上下文，不接受客户端提供另一租户。MemoryService 对运行上下文中的 `tenant_id` 与重新查出的用户归属做一致性校验，避免伪造或陈旧的 Agent 上下文访问错误 namespace。

## 修改步骤

- [x] **数据库和初始化**：在 `database/schema.py` 把唯一租户外键、角色、状态放入 `users` 并删除 `user_tenants`；从 `database/constants.py` 的 `BUSINESS_TABLES` 移除该表。调整 `repository/seed_data.py` 与 `repository/bootstrap.py`，每个演示用户直接携带一个租户 ID；重复初始化不得悄悄改变已有用户的租户归属。
- [x] **Repository 和业务上下文**：`repository/users.py` 改为按 `user_id` 读取唯一用户及租户，移除可选租户参数、排序取首条和多租户分组；`repository/mappers.py` 的开发用户响应改为单值租户信息，删除 `tenant_ids`、`tenant_memberships`、`default_tenant_id` 等候选列表字段。`repository/models.py` 保留服务端内部需要的租户上下文字段。
- [x] **服务与 Agent**：移除 `services/chat.py`、`conversations.py`、`attachments.py`、`execution.py`、`user_input_execution.py` 中逐层透传的 `tenant_id` 参数；每个独立执行或恢复入口按 `user_id` 重新解析上下文。保留 `core/agent.py` 的服务器端租户上下文；修改 `memory/service.py` 的再校验逻辑，确保 namespace、权限和事件使用该用户唯一且有效的租户。
- [x] **HTTP 协议**：从 `api/schemas.py` 以及项目、会话、聊天、附件、审批、用户问题、模型等路由的 JSON、查询参数和 multipart 表单中移除 `tenant_id`。保留附件路由中 `api/identity.py` 对 `user_id` 的现有开发身份入口；`GET /api/dev/users` 返回单个租户信息。同步 API 类型契约，不保留旧参数分支。
- [x] **前端**：在 `frontend/src/types/api.ts`、`api/client.ts`、`api/stream.ts` 移除请求中的 `tenantId`；在 `state/session.tsx` 移除租户候选、默认选择、独立 `tenantId` 状态和切租户判等逻辑，用户切换仍负责清理旧会话上下文及阻挡迟到响应。`state/storage.ts` 停用租户本地存储键，模型选择改为按用户存储。清理 `useChatStream.ts`、`Sidebar.tsx`、`Composer.tsx`、`ChatView.tsx`、`AttachmentDialog.tsx` 的租户参数与依赖；展示租户名称时从当前用户资料读取。
- [x] **文档**：更新 `README.md` 的开发模拟用户和建库说明、`docs/ARCHITECTURE.md` 的归属与上下文规则，以及涉及租户校验的设计文档。方案确定并实现后，形成稳定设计文档并更新 `docs/design-docs/index.md`；执行结束后将本计划移入 `docs/exec-plans/completed/`。

## 失败与安全边界

- 用户不存在或 `tenant_status` 不为 `active` 时，所有需要用户上下文的 API 拒绝请求；不能从租户列表中选择替代值。
- 业务资源仍必须按 `user_id` 校验所有权。单租户归属不提供生产认证，也不赋予同租户用户访问彼此 Project、Conversation 或附件的权限。
- Tenant Memory 的直接发布继续检查该用户的租户角色；Agent 的请求上下文与数据库归属不一致时拒绝 Memory 操作。
- 数据库清空会一并丢失业务记录、LangGraph Checkpoint 和 Store 记忆。改变用户租户映射并重建数据库时，必须备份所需文件并同时清理配置的旧 workspace 根目录；建表命令不负责清理，沿用旧目录会留下失去数据库索引的文件。

## 验收标准与验证计划

1. 新建数据库执行 `uv run melonclaw-db-init` 后，`users.tenant_id` 非空且引用 `tenants`，没有 `user_tenants` 表；重复执行种子初始化结果稳定。
2. 当前用户不传 `tenant_id` 可完成项目、会话、附件、消息和恢复流程；API 契约与前端请求均不再声明或发送该字段。
3. 一个用户只能解析出一个租户。无效状态的用户被拒绝；Memory 用该租户 namespace，租户记忆写入按角色授权；运行上下文与数据库租户不一致时拒绝操作。
4. 切换开发用户后，会话与模型选择只按用户隔离；聊天流、审批、用户问题和附件流程继续使用新用户的唯一租户上下文。
5. 更新相关后端与前端测试；记录自动化检查、数据库初始化和未覆盖的真实模型流程。

## 验证记录

- 已清空本机 `melonclaw` 数据库中的 16 张已知旧表，随后两次运行 `uv run melonclaw-db-init` 均成功。数据库中 `users.tenant_id`、`tenant_role`、`tenant_status` 均为非空，`user_tenants` 不存在，三个演示用户分别归属魏、蜀、吴。
- 本次代码改造期间，后端 `uv run pytest -xq tests` 为 95 passed，前端 `npm test -- --silent` 为 21 files / 156 tests passed；后续复审增加种子冲突与 API 契约用例，并以 `scripts/check.sh` 重新覆盖完整检查。
- `scripts/check.sh` 全部通过，覆盖编译、pytest、ruff、锁文件、前端 lint、类型检查、vitest、文档链接和 diff 检查。未调用真实模型验证消息、Memory、审批和用户问题恢复。
- 工作区旧文件保留在当前配置的 workspace 根目录；数据库重建不负责删除它们。

复审补充：给 PostgreSQL 冲突更新无返回行的 fail-fast 路径加注释和单测；演示用户显式写入 `member/active`，冲突使用 `SeedDataConflictError`；会话移动测试替身改为完整 `UserContext`；开发用户响应移除重复的租户名称字段，继续通过已使用的 `display_name` 展示“用户-租户”。新增 API 契约测试防止重新声明客户端 `tenant_id`；架构和使用文档明确模拟 `user_id` 可冒用，以及改变租户映射时要同时清理旧工作区。复审后 `scripts/check.sh` 全部通过。

真实模型 smoke：在临时 PostgreSQL 数据库和独立 workspace 中使用 `.env` 配置的 DeepSeek 运行 FastAPI lifespan 与 HTTP 请求。普通消息完成；模型调用 `remember_user_memory` 写入 user Memory，读取确认后删除；`write_file` 触发审批，拒绝后恢复完成且文件未创建；`ask_user` 触发问题卡片，提交选项后恢复完成。所有临时数据已清理。可选 MCP 服务中有四个初始化返回 HTTP 401，因此本次 smoke 不覆盖这些 MCP；这不影响上述四条应用内链路。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-09-25 | 提案采用 `users.tenant_id` 的直接归属，并删除 `user_tenants` | 当前每个用户只需要一个租户上下文；减少可选租户参数和多份状态。 |
| 2026-09-25 | 不迁移历史数据库与浏览器存储 | 项目处于开发期，允许重建数据；避免保留与目标模型相冲突的兼容分支。 |

## 风险与依赖

- 前端原有租户 ID 参与大量请求参数、异步回调判等和模型本地存储键；现已删除，用户切换和并发流继续按用户与 generation 失效机制处理。
- 当前 `user_id` 可以由开发页面提交；本方案不改变这一身份信任边界。面向共享环境部署仍需接入可信认证和隔离后端。
- 已清空本地 `melonclaw` 数据库并运行 `uv run melonclaw-db-init`。服务启动只校验表结构，不负责建表或数据转换；原工作区文件未删除。
