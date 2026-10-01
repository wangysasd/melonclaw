# 用户唯一租户归属

状态：已实现。

## 背景与目标

原来的 `users` 与 `tenants` 通过 `user_tenants` 多对多关联。请求未指定租户时，仓储会按租户 ID 排序后取第一条；前端还保存一份租户选择状态。Project、Conversation 和附件却始终归用户所有，同一会话可能在不同请求中得到不同的 Tenant Memory 上下文。

目标是让每名用户始终有且只有一个租户归属，移除请求和浏览器中的租户选择。项目仍处开发期，改表时清空数据库；不保留旧数据结构、旧 API 参数或本地存储的兼容分支。

## 方案概览

- `tenants` 可拥有多名用户；`users.tenant_id` 是非空外键，`tenant_role` 与 `tenant_status` 位于用户行。业务 Schema 不再创建 `user_tenants`。
- 用户租户归属是当前运行模型中的固定属性，没有更换租户的业务 API。演示种子显式写入 `tenant_role=member`、`tenant_status=active`；重复初始化只更新同租户用户的名称，已有用户归属与种子冲突时抛出 `SeedDataConflictError` 并要求重建数据库。
- API 仅接收开发模拟 `user_id`，不得定义或消费客户端传入的 `tenant_id`。服务层按该 ID 查找状态为 `active` 的用户及其租户，再按 `user_id` 校验 Project、Conversation 和附件的归属。
- Agent 运行上下文保留服务端读取的 `tenant_id` 和租户角色。Global / Tenant / User Memory 的 namespace 保持原结构；Tenant Memory 和审计记录使用该租户 ID。Memory 操作再次读取用户归属，并拒绝与运行上下文不一致的租户。

## 数据流与关键取舍

```text
浏览器选择模拟用户并提交 user_id
  → 服务层从 users → tenants 解析唯一有效归属
  → 仓储按 user_id 校验业务资源所有权
  → AgentContext 使用解析得到的租户 ID 和角色
  → Memory 校验归属、选择 namespace、记录审计事件
```

会话仍只保存 `user_id + project_id` 归属，不重复存 `tenant_id`。审批恢复、用户问题恢复、取消和过期问题收尾都重新解析当前用户；它们不能通过请求选择别的租户。前端切换用户会清理会话上下文和旧流，模型选择按用户保存在本地。

## 失败与安全边界

- 用户不存在或 `tenant_status` 非 `active` 时，业务请求不能得到用户上下文。不存在“改选另一个租户”或按 ID 排序选第一个的路径。
- 同租户用户的 Project、Conversation 和附件按 `user_id` 校验归属。但浏览器可伪造开发模拟 `user_id`，所以当前隔离不能抵御主动冒用；共享部署需要让所有受保护路由统一使用可信身份来源，并隔离 Agent 的文件和 Shell 能力。目前受信任身份头只接入附件路由。
- Tenant Memory 的直接发布仍要求 `admin` 或 `owner` 角色。Agent 上下文中的租户与数据库归属不一致时，Memory 拒绝访问。
- 清空数据库会删除业务记录、Checkpoints 和 Store 记忆。改变用户租户映射时，必须备份所需文件并同时清理配置的旧工作区根目录；建表命令不会删除工作区，旧文件可能失去数据库索引。工作区路径不是租户隔离边界。服务启动不会建表或迁移。

## 运行与验证

停止服务并清空目标开发数据库后，执行 `uv run melonclaw-db-init` 建表和写入演示用户，再启动 Web。预期 `users.tenant_id` 非空、`user_tenants` 不存在、演示用户各有一个租户，前端请求不包含 `tenant_id`。检查项目、会话、附件、消息和恢复路径是否从当前用户解析同一租户；检查无效用户和租户不匹配的 Memory 上下文被拒绝。

代码契约检查见 `tests/test_schema.py`、`tests/test_bootstrap.py`、`tests/test_tenant_contract.py` 及前端测试；真实 PostgreSQL 初始化与应用请求的观察结果以执行计划的记录为准。


## 当前开发数据中的部门租户

开发数据库中另有两个显式部门租户：`dep-a`（部门A）和 `dep-b`（部门B）。张三的 `users.tenant_id` 指向 `dep-a`，李四指向 `dep-b`。新租户 ID 与中文名称在 `repository/seed_data.py` 登记，确保重复执行 db-init 时租户记录仍在。用户租户关系以 `users.tenant_id` 为准；改归属需要在事务中先确保租户存在，再更新该列。当前用户创建接口仍将新用户放入 `system` 租户，没有面向普通用户的换租户 API。
