# 手机验证码忘记密码（找回密码）

状态：已完成
开始日期：2026-11-28

## 目标

在现有短信认证与账户密码链路上新增「忘记密码」功能：已绑定手机号的有效普通用户可通过手机验证码验证身份后自助重置密码，并使用新密码重新登录。短信关闭时隐藏入口且接口返回 403，原密码登录不受影响。

## 背景

当前登录页支持账号密码登录、短信验证码登录与自助注册，已有普通用户支持首次绑定手机号，但缺少忘记密码自助找回入口（技术债 D18）。用户忘记密码后只能借助管理员或短信登录，无法自助重置密码。

现行契约见[短信注册与登录](../../design-docs/sms-registration-login.md)与[MelonClaw 浅蓝登录页](../../design-docs/melonclaw-login-page.md)。

## 步骤

- [x] 扩展配置（可选 `MELONCLAW_SMS_TEMPLATE_RESET`）、`schema.py` 与增量迁移 `20261128_sms_reset_password`
- [x] 后端仓储、短信认证服务、账户服务、路由、匿名路径与安全审计
- [x] 前端 API、草稿恢复、`AuthPanel` 找回密码表单与登录页底部入口布局
- [x] 后端单测、隔离 PostgreSQL 迁移测试与前端登录单测
- [x] 同步设计文档、技术债、配置说明、环境变量模板与 README
- [x] 运行 `scripts/check.sh` 校验并记录验证结果

## 验收标准

- 新库 `schema.py` 与旧库 `20261128_sms_reset_password` 均允许 `sms_challenges.purpose = 'reset'`，升级保留现有数据且可重复执行。
- 短信关闭时：`POST /api/auth/sms/challenges`（`purpose="reset"`）与 `POST /api/auth/password/reset` 均返回 403；登录页不显示「忘记密码」。
- 短信开启时：已绑定手机号的有效普通用户发送 `reset` 验证码后可重置密码，旧密码失效、新密码登录成功，原 `registration_verifications` 记录不被改写；未绑定手机号、已删除用户、跨用途验证码、错误码、超时、重放和并发提交均被拒绝且不改密。
- 审计日志记录操作「找回密码」及 `尾号XXXX`，不含完整手机号、验证码或密码。
- 登录页底部提供「忘记密码」与「注册账号」入口（短信开启时），支持进入找回密码视图、隐私说明草稿保存与重置后返回密码登录。
- `scripts/check.sh` 通过（跳过项单独说明）。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-28 | 新增短信用途 `reset` 并通过增量迁移 `20261128_sms_reset_password` 扩展 `ck_sms_challenges_purpose` | 复用现有 `sms_challenges` 状态机、配额和单活跃约束，不改写已登记的 `20261126_sms_auth`，保留历史数据 |
| 2026-11-28 | `MELONCLAW_SMS_TEMPLATE_RESET` 设为可选，留空回退 `MELONCLAW_SMS_TEMPLATE_AUTH` | 允许配置阿里云找回密码专用赠送模板，同时避免已有开启短信的部署升级后因缺变量无法启动 |
| 2026-11-28 | 重置接口返回 `{"ok": true, "user_id": ...}`，前端跳回密码登录页并预填用户 ID 与成功提示 | 验证码核验通过后才返回 `user_id`，与短信登录等效，不泄露未注册账号；让用户用新密码登录确认，且不提前作废既有无状态 JWT |
| 2026-11-28 | 在 `tests/conftest.py` 隔离 `MELONCLAW_SMS_*`、`MELONCLAW_REGISTRATION_ENABLED` 与 `MELONCLAW_PHONE_HMAC_KEY` | 防止本机 `.env` 已启用的真实短信配置污染单元测试默认关闭断言 |

## 验证记录

2026-11-28：

- 通过：`scripts/check.sh` 全部通过（`git diff --check`、`compileall`、`pytest` 含架构依赖与文档链接校验、`ruff`、`uv lock --check`、`eslint`、`tsc --noEmit`、`vitest`）。
- 覆盖：`tests/test_sms_auth.py` 覆盖模板回退与显式指定、短信关闭 403、跨站 403、多余字段 422、密码不一致/过短前置校验、跨用途拒绝、错误码重试、锁冲突退回重试、重置后旧密码失效与新密码登录、既有 JWT 保留、重放拒绝、未绑定号码消费拒绝、已删除用户拒绝、`registration_verifications` 不变及审计脱敏；`frontend/tests/account-login.test.tsx` 覆盖短信关闭隐藏入口、找回密码完整流程、返回密码登录预填用户 ID、成功提示与隐私说明草稿恢复。
- 跳过：`tests/test_sms_postgres.py`（未配置 `MELONCLAW_TEST_DATABASE_URL`，隔离 PostgreSQL 升级用例跳过，不计作通过）。
- 未运行：真实阿里云短信发送与浏览器实机端到端验证。

## 风险与依赖

- 已有库升级前需停服备份并执行 `uv run melonclaw-db-update` 以应用 `20261128_sms_reset_password`。
- 真实阿里云短信发码依赖部署环境凭据与模板配置，如需使用阿里云找回密码专用赠送模板，可额外配置 `MELONCLAW_SMS_TEMPLATE_RESET`。
