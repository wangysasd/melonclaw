# 用户／租户管理与开发登录

状态：已实现。日期：2026-10-06。

## 背景与目标

在现有应用中提供最小账户管理和登录入口，保留所有用户都能切换身份的开发便利。不是生产多租户认证系统：仅 profile=dev 时，任何已登录用户均可显式切换为 admin；非 dev 禁用切换接口及入口。

## 数据与权限

- 复用 users 和 tenants，一个用户通过非空 users.tenant_id 属于一个租户；不创建关系表。名称上限 64 字符，ID 为 1～64 位字母、数字、下划线或连字符，首字符必须为字母或数字。ID 创建后不可修改／复用。
- users.password_hash 保存带随机盐的 scrypt 哈希；NULL 表示未设置密码，不能密码登录。tenant_status 为 active/deleted，删除只更新状态。created_at/updated_at 记录时间。
- tenants.enabled 控制启停，不提供删除。名称保持唯一。system 不可停用，admin 不可删除或移出租户。
- auth_sessions 是辅助会话表，不是关系表。只保存随机 Token 的 SHA-256 摘要，7 天过期。Cookie 为 HttpOnly、SameSite=Strict，HTTPS 时设置 Secure。
- 会话的 user_id 是当前操作身份；login_user_id 仅用于重置原账号密码时撤销相关会话，不参与授权。修改密码、删除用户、停用租户均撤销相关会话；每次请求重新校验当前用户和原登录账号有效性。
- 系统管理仅当前身份为 system/admin 时开放。仅 profile=dev 时所有用户可以调用显式切换接口进入有效用户，包括 admin；切走后无残留管理员权限。

## 数据与事件流

```text
密码验证或 dev 免密 → 建立 Cookie 会话 → 服务端解析当前用户
→ 前端加载该用户项目和会话 → 服务端解析其唯一租户 → Agent/Memory 上下文

切换用户 → 服务端更新会话 → 通知同页和其他标签页 → 卸载旧工作区
→ 清除目标用户上次项目／会话选择 → 首页加载新身份
```

已有业务请求中的 user_id 只作“页面仍属于该用户”的断言，必须与会话匹配；附件／下载直接从验证后的会话解析身份。X-Melonclaw-User 同样只是旧页面检测，不是可信身份来源。旧的 MELONCLAW_IDENTITY_HEADER 入口和无密码创建用户接口已移除。

用户更换租户只改 users.tenant_id：项目、会话、文件、个人配置和 User Memory 都留在用户下；Tenant Memory 留在原租户。历史聊天中已出现的租户信息不会被擦除。普通业务 API 不接受 tenant_id 来覆盖运行上下文，只有管理资料接口允许提交目标租户。

Agent 缓存键包含当前租户，换租户后不会命中旧租户实例；MemoryService 继续重新校验归属。不同用户的个人项目不会因同租户自动共享。

## 界面与 API

登录页复用品牌图片，以 14px 为主要字号。包含用户 ID、密码和登录按钮，无注册或找回密码。登录表单关闭浏览器自动填充，使用独立字段名，密码字段标记 `new-password`，避免把同源页面保存的模型 URL/API Key 回填为登录凭据。仅根目录 `.env` 小写 `profile=dev` 时显示“免密登录”，后端同时开放入口。空、未配置、DEV 或其他值均关闭。前端读取布尔能力，不读取环境文件。

全局导航底部：当前用户为 admin 时显示“系统管理”，包含用户管理／租户管理。用户创建需 ID、名称、租户、初始密码及再次输入密码确认；编辑不带密码，修改密码单独操作。租户下拉显示名称（ID）。支持搜索、租户筛选、基础分页。租户可修改名称及启用状态，无删除功能。

仅 profile=dev 时所有用户显示用户切换器；有未发送内容时确认。切换失败保留当前页面。头像菜单“登出”撤销服务器会话，卸载个人数据并返回登录页；失效身份不会自动回退 admin。跨标签页通过 storage 通知刷新，旧请求身份不匹配返回 409。

| API | 用途 |
|---|---|
| GET /api/auth/config | 匿名读取免密开关 |
| POST /api/auth/login | 用户 ID + 密码建立会话 |
| POST /api/auth/passwordless | 仅 dev 建立 admin 会话 |
| GET /api/auth/session | 获取当前有效身份 |
| POST /api/auth/switch | 已登录后显式切换用户 |
| POST /api/auth/logout | 撤销会话并清 Cookie |
| GET /api/dev/users | 已登录用户读取可切换用户 |
| GET /api/admin/users、POST /api/admin/users | 列表／创建用户 |
| PATCH /api/admin/users/{user_id}、DELETE /api/admin/users/{user_id} | 编辑／软删除用户 |
| POST /api/admin/users/{user_id}/password | 管理员修改指定用户密码 |
| POST /api/auth/password | 已登录用户修改当前身份密码 |
| GET /api/admin/tenants、POST /api/admin/tenants | 列表／创建租户 |
| PATCH /api/admin/tenants/{tenant_id} | 修改名称、启停 |

## 失败、并发与安全边界

账户管理走 services/accounts.py 和 repository/accounts.py。新能力不是 Agent 工具，不加入 HITL 或 PTC，也不改变既有工具授权。

普通业务请求在统一 API 依赖中持有 PostgreSQL 共享事务 advisory lock；管理写入尝试非阻塞独占锁，冲突返回 409。该开发版锁是全局粒度，其他用户正在处理请求也可能暂时阻止管理写入，稍后重试即可。此选择避免单机内存锁无法覆盖多进程，但不以高并发管理吞吐为目标。

用户删除、换租户和租户停用还检查 pending/interrupted 的助手消息，防止操作运行中或等待审批／回答的任务。服务端运行准备与身份修改不会竞态穿透。前端切换不取消后台 Agent：SSE 使用有界队列，断开后停止投递但继续消费业务执行，结果仍写数据库；服务关闭才取消后台执行并释放资源。此行为不提供跨服务重启的自动任务恢复。

密码和 Token 不回显；账户输入校验错误统一返回安全文案。写接口校验 Origin / Sec-Fetch-Site，部署使用同源反代（保留 Host），不支持通过任意跨域前端携带 Cookie 写入。登录、切换和管理请求仍只适合受信任的本机开发；LocalShellBackend 不是沙箱。

## 初始化与运行

```bash
uv sync --locked
uv run melonclaw-db-update  # 已有系统：新增用户、租户与登录表结构并保留数据
scripts/start.sh
```

首次上线使用 `uv run melonclaw-db-init`。从本功能之前的版本升级时，停止旧服务后运行 `uv run melonclaw-db-update`，已有用户、租户和业务数据会保留；服务启动本身不执行数据库升级。首次初始化 admin 密码固定为 `admin`，数据库仍保存哈希，直接使用 `admin/admin` 登录。需要修改或找回时可选执行 `uv run melonclaw-admin-password`，交互输入两次、不回显、不接收密码命令参数。密码长度统一为 5～128 字符。重复 db-init 不覆盖账户名称、密码、归属和删除状态。

本机需要免密入口时在 `.env` 设置 `profile=dev` 后重启后端；`.env.example` 默认为空。无可用模型时依旧提示先配置模型，不自动创建模型。

## 验证

- tests/test_account_api.py：profile 矩阵、认证、全员切换、管理员权限、请求身份断言、跨站拒绝和登出撤销。
- tests/test_user_admin.py：密码哈希、输入校验和内置账号保护。
- tests/test_accounts_postgres.py：临时独立 schema 中真实验证 CRUD、锁冲突、挂起任务阻止修改、软删除保留数据、换租户保留项目、密码重置／租户停用／过期撤销。
- tests/test_database_updates.py：旧 users/tenants schema 的升级、原有行保留、字段扩长、admin 初始化哈希和重复运行。
- tests/test_background_stream.py：断开订阅不取消执行，服务关闭释放执行。
- frontend/tests/account-login.test.tsx：登录元素、免密能力开关、失败保留输入和身份失效。

真实数据库集成测试需设置 MELONCLAW_TEST_DATABASE_URL（只从环境读取凭据），执行 `uv run pytest -q tests/test_accounts_postgres.py`。测试只创建、清理自己生成的临时 schema，不改 public 数据。无测试连接时默认跳过。检查汇总见[执行计划](../exec-plans/completed/user-management-login.md)。

### 密码确认与开发入口补充

创建用户、本人修改密码和管理员修改密码均提交 password 与 confirm_password，前后端校验一致；无需旧密码。本人入口位于头像菜单，服务端仅使用会话当前用户，不接受目标用户 ID。修改成功撤销相关会话；本人返回登录页。开发切换与免密登录共用 profile=dev 条件，非 dev 接口返回 403。数据库升级脚本为 `20261006_account_management`，由 db-update 事务应用并记入 `melonclaw_schema_updates`。旧库执行 `uv run melonclaw-db-update` 后可保留现有用户、租户和业务数据；新库执行 db-init。

验证结果：`scripts/check.sh` 全部通过（后端测试与 lint、前端 lint／类型检查／测试、文档链接与空白检查）；真实 PostgreSQL 独立临时 schema 生命周期测试通过（1 passed），未修改现有业务数据。
