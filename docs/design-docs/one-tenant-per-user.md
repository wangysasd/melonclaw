# 用户唯一租户归属

状态：已实现。2026-10-06 更新。

## 背景与目标

原有多对多 user_tenants 在未指定租户时需要任取一个成员关系，容易使同一会话的 Memory 上下文不稳定。当前每名用户始终有且只有一个租户，移除独立成员关系表及前端租户选择。

## 当前方案

- tenants 与 users 一对多，users.tenant_id 是必填外键；tenant_role 与 tenant_status 位于用户行。
- 当前 system/admin 可以通过用户管理创建用户并指定租户，也可修改归属。更换归属不迁移个人数据；租户记忆留在租户。存在未完成任务时拒绝变更。
- Web 使用可撤销 Cookie 会话。业务请求 user_id 必须与当前会话身份一致；租户只能从服务端用户记录解析。只有管理用户资料接口允许提交目标 tenant_id，普通业务接口不能覆盖运行租户。
- 项目、会话、附件和个人配置仍按 user_id 校验所有权。Conversation 仅记录 user_id + project_id，不重复存储租户。
- Global / Tenant / User Memory 命名空间维持原结构。Memory 操作再次读取用户归属，拒绝与运行上下文不一致的租户；租户记忆发布仍遵守既有角色授权。
- Agent 缓存键包含租户，变更归属后使用新实例。已出现于历史消息的旧租户信息不会被删除。

## 数据流

```text
Cookie 会话 → 当前有效用户 → users.tenant_id → 启用中的租户
→ 按用户校验 Project / Conversation / 附件 → AgentContext → Memory 再校验
```

仅 profile=dev 时，所有已登录用户可显式切换到其他用户（包括 admin），因此本方案是开发使用，不承诺生产权限隔离。profile 同时控制免密登录及用户切换，空值、未配置或其他值关闭两者。用户删除为软删除；租户不删除，仅启停。

## 初始化与验证

表结构由 schema.py 定义，db-init 使用 create_all，不迁移旧库。重复初始化只补录缺失种子，不覆盖密码、资料、归属或状态。seed_data.py 中保留 system、dep-a、dep-b 租户模板，唯一种子用户为 system 下的 admin。

数据库结构变化时，先停止服务并保留所需数据，再清空重建目标开发库并执行 `uv run melonclaw-db-init`；首次初始化后使用 `admin/admin` 登录；`uv run melonclaw-admin-password` 作为可选改密命令。单纯通过管理页面修改用户租户，不需要清库或清理工作区。

测试见 tests/test_schema.py、tests/test_tenant_contract.py、tests/test_accounts_postgres.py。完整界面、API、运行及并发边界见[用户管理与开发登录](user-management-login.md)。
