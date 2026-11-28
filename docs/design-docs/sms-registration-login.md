# 短信注册与登录

状态：已实现。日期：2026-11-26。

账户、权限和开发入口见[用户管理与开发登录](user-management-login.md)。令牌见[JWT 登录](jwt-login.md)。每个用户仍只属于一个租户，见[用户唯一租户归属](one-tenant-per-user.md)。

## 目标

新用户在登录页注册账号并验证中国大陆手机号，成功后直接进入工作台。之后可以用账号密码或短信验证码进入同一个用户、租户和工作区。已有普通用户可以首次绑定手机号，并在忘记密码时通过已绑定手机号的验证码自助重置密码。短信关闭或云服务失败时，密码登录仍然可用。

换绑和解绑不在本版范围。

## 用户流程

MelonClaw 和 RMS 共用 `AuthPanel`。默认是账号密码登录。服务端打开对应开关后，页面才显示「短信验证码登录」、「忘记密码」和「注册账号」。

| 入口 | 用户操作 | 服务端结果 |
|---|---|---|
| 注册账号 | 填写用户 ID、密码、确认密码和手机号，获取并填写验证码 | 核验通过后，在同一事务中创建独立租户、普通用户、手机号绑定和注册核验记录，提交后签发 JWT。初始昵称使用用户 ID，登录后可在头像菜单修改 |
| 账号密码登录 | 填写现有用户 ID 和密码 | 沿用现有密码哈希校验和 JWT |
| 短信验证码登录 | 填写已绑定手机号并提交登录验证码 | 核验通过后按手机号摘要查找有效账户。未注册号码即使收到验证码，也只提示先注册，不创建账户 |
| 忘记密码 | 填写已绑定手机号、获取并填写找回密码验证码，设置新密码与确认密码 | 核验通过后按手机号摘要查找有效普通账户，在同一独占账户事务中核销验证码并更新密码哈希，返回对应用户 ID；前端切回账号密码登录并提示使用新密码登录。未绑定号码、已删除用户和内置管理员均被拒绝；不改写注册核验记录，不提前作废既有 JWT |
| 首次绑号 | 已登录普通用户在账户菜单中确认当前密码，并验证新手机号 | 绑定目标来自当前 JWT。已有手机号时拒绝再次绑定 |

倒计时使用服务端返回的 `resend_after`。更换手机号或切换入口会清除验证码和请求编号。密码和验证码只留在当次表单。注册、短信登录和找回密码在手机号标签的信息图标中显示对应用途说明，并链接无需登录的[隐私说明](privacy-notice.md)。打开说明不会发短信；返回后当前标签页恢复已填表单，登录或重置成功后清除。绑号对话框链接同一页面。

自助注册不接受租户或角色。服务端生成租户 ID 和唯一名称，用户以 `member` 加入。内置管理员继续使用现有密码登录，不能绑定手机号。

## 云产品

使用阿里云号码认证服务的短信认证，由服务端 SDK 调用 `SendSmsVerifyCode` 和 `CheckSmsVerifyCode`。验证码由阿里云生成，`ReturnVerifyCode` 为 false，不把明文验证码取回。发送参数为 6 位数字、有效期 300 秒、间隔 60 秒、`DuplicatePolicy=1`（新码覆盖旧码）、`AutoRetry=0`、`CodeType=1`。模板变量使用 `##code##`，分钟数按有效期向上取整。登录和注册使用同一套赠送模板，绑定使用另一套，找回密码支持单独配置模板（留空时沿用登录/注册模板）；用途由本服务的验证请求区分。

方案名由后端按环境和用途生成，不超过 20 个字符：`dev-register`、`prod-login`、`prod-reset`、`app-bind`。未识别的 profile 使用 `app`。发码和核验使用同一次保存的方案名和外部流水号。

核验必须同时满足 `Success=true`、`Code=OK` 和 `Model.VerifyResult=PASS`。`UNKNOWN` 表示核验失败，剩余次数内可以重试。超时、响应缺字段或进程在核验中退出时，本次请求无法用于注册或登录，冷却后可以重发。发送超时不会自动重试。[阿里云核验接口](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-checksmsverifycode)

## 数据

`20261126_sms_auth` 新增绑定、验证请求和配额三张表。`20261126_registration_verification` 另建注册成功记录表。`20261128_sms_reset_password` 扩展 `sms_challenges.purpose` 约束以支持 `reset`。三步都保留已有用户、密码和租户。

| 表 | 保存内容 |
|---|---|
| `user_phones` | 用户 ID、手机号 HMAC-SHA256、4 位尾号。用户和摘要各自唯一 |
| `sms_challenges` | 用途、号码摘要、绑号用户、代次、状态、外部流水号、方案名、尝试次数和到期时间 |
| `sms_quotas` | 号码间隔、号码日额度、来源 IP 日额度和全站日额度。重启后保留 |
| `registration_verifications` | 用户 ID、唯一验证编号、方式 `sms_code`、4 位尾号和核验成功时间。注册时间复用 `users.created_at` |

手机号先规范成 `+86` 加 11 位国内号码，再计算摘要。业务库不保存完整号码。摘要密钥是 `MELONCLAW_PHONE_HMAC_KEY`，至少 32 个字符，纳入部署备份。启用后不能随重启更换；轮换需要单独迁移。

同一号码和用途同时只有一条 `reserved`、`sent` 或 `verifying` 请求。重发把旧请求标为 `replaced`。管理员删除用户时，同一事务释放手机号绑定，号码可以重新注册新账号。此前删除的账号保留的绑定在重新注册核验成功时释放。已删除账号及其注册核验记录保留，原用户 ID 不能重复注册。

验证请求和配额在下次发送时删除创建或更新早于 48 小时的行。这与验证码有效期不是同一件事；有效期默认 300 秒，可配置为 60 到 1800 秒。没有新的发送请求时，这些行不会按固定日程清空。

注册成功记录以用户 ID 为主键，验证编号复用系统生成的 `challenge_id`，单独加唯一约束。没有指向短期验证请求的外键，清理后仍可按账号或编号核对。核验时间取后端收到通过结果的时间，注册时间取建户时间。后续登录、绑号和用户软删除都不会改写该记录。已有账号不做回填。

## 接口

| 方法 | 认证 | 行为 |
|---|---|---|
| `GET /api/auth/config` | 匿名 | 增加 `registration` 和 `sms_login` 布尔值 |
| `POST /api/auth/sms/challenges` | 匿名 | 用途为 `register`、`login` 或 `reset`。返回 `challenge_id`、`expires_in`、`resend_after`。不透露号码是否已注册 |
| `POST /api/auth/register` | 匿名 | 注册并返回现有 `access_token` |
| `POST /api/auth/login/sms` | 匿名 | 登录并返回同一结构 |
| `POST /api/auth/password/reset` | 匿名 | 校验手机号验证码并重置有效普通用户密码，返回 `ok` 与 `user_id` |
| `POST /api/auth/phone/challenges` | 登录 | 为当前普通用户发绑定验证码 |
| `POST /api/auth/phone/bind` | 登录 | 校验当前密码和验证码后写入绑定，返回尾号 |
| `GET /api/admin/users` | 系统管理员 | 返回注册核验状态、方式、尾号、时间和编号。`include_deleted_registrations=true` 同时返回有记录的软删除用户 |

这些匿名路径加入 `PUBLIC_PATHS`，仍检查 Origin，并拒绝多余字段。短信开关独立于 dev 免密。关闭时对应操作返回 403，密码登录不受影响。

发码前在短事务中原子扣减配额并预留请求，然后才调用阿里云。超限返回 429、`Retry-After` 和等待秒数。默认同一号码跨用途 60 秒 1 条、每天 10 条，同一来源 IP 每天 30 条，全站每天 200 条，每次请求最多尝试 5 次。全站上限应按短信费用调整。

核验先条件更新抢占请求并增加尝试次数，再在事务外调用阿里云。收到明确的 PASS 后，账户事务按当前时间复查请求状态、代次、用途、手机号摘要和绑号用户，核销再和业务变更一起提交。任何注册写入失败都会回滚租户、账号、绑定和注册记录。条件更新与唯一约束保证同一请求并发提交最多创建一个账号、一个租户和一条注册记录。注册已提交但响应丢失时，用户可以用刚设置的密码登录。

注册和绑号使用现有独占账户锁。锁冲突时回滚本次创建，并把验证请求退回可重试状态。账号冲突只在手机号核验通过后提示。

## 管理员查阅

使用 `system/admin` 打开「系统管理 → 用户管理」，按用户 ID 或验证编号搜索。点击「手机号已验证」可查看用户 ID、注册时间、核验成功时间、短信验证码方式、手机尾号和验证编号。勾选「含已删除的注册账号」可查阅软删除账号的原记录。状态来自实际登记的自助注册记录，内置管理员和后台创建的账号不标记「手机号已验证」。普通用户没有该列表的读取权限。

### 对外账号核验范围

自助注册只有在手机号短信验证码校验通过后才能创建账号，系统留存注册核验记录。存量普通账号和后台创建的普通账号仍可用密码登录；首次绑号形成手机号绑定，不产生注册核验记录。内置管理员使用密码登录，不支持短信绑定。管理端没有核验记录时，按账号开设来源和实际资料判断，不能由是否绑定手机号反推注册时已经核验。短信验证确认用户可接收该号码的验证码，系统未核验姓名与证件的一致性。

正式站点的现场验收及脱敏取证步骤见[安全自评与监管协查](security-assessment-operations.md)。

## 配置

全部经 `core/config.py` 读取。变量名使用 `MELONCLAW_` 前缀，因此不会进入 Agent 环境。

| 变量 | 规则 |
|---|---|
| `MELONCLAW_SMS_ENABLED` | 默认 false。true 时才允许发码、短信登录和绑号 |
| `MELONCLAW_REGISTRATION_ENABLED` | 默认 false。true 时必须同时启用短信 |
| `MELONCLAW_SMS_ACCESS_KEY_ID` / `MELONCLAW_SMS_ACCESS_KEY_SECRET` | 启用时必填。RAM 权限为 `dypns:SendSmsVerifyCode` 和 `dypns:CheckSmsVerifyCode` |
| `MELONCLAW_SMS_SIGN_NAME` | 启用时必填，使用控制台赠送签名 |
| `MELONCLAW_SMS_TEMPLATE_AUTH` | 登录/注册赠送模板 Code |
| `MELONCLAW_SMS_TEMPLATE_BIND` | 绑定新手机号赠送模板 Code |
| `MELONCLAW_SMS_TEMPLATE_RESET` | 可选，找回密码赠送模板 Code；留空时回退 `MELONCLAW_SMS_TEMPLATE_AUTH` |
| `MELONCLAW_PHONE_HMAC_KEY` | 启用时必填，至少 32 个字符 |
| `MELONCLAW_SMS_CODE_VARIABLE` / `MELONCLAW_SMS_MINUTES_VARIABLE` | 默认 `code` 和 `min` |
| `MELONCLAW_SMS_PHONE_INTERVAL_SECONDS` | 默认 60 |
| `MELONCLAW_SMS_PHONE_DAILY_LIMIT` | 默认 10 |
| `MELONCLAW_SMS_IP_DAILY_LIMIT` | 默认 30 |
| `MELONCLAW_SMS_GLOBAL_DAILY_LIMIT` | 默认 200 |
| `MELONCLAW_SMS_MAX_ATTEMPTS` | 默认 5，最大 10 |
| `MELONCLAW_SMS_CODE_TTL_SECONDS` | 默认 300，允许 60 到 1800 |
| `MELONCLAW_SMS_TIMEOUT_SECONDS` | 默认 8，最大 30 |

短信关闭时，缺少上述凭据不会阻止启动。公开运营主体和申请邮箱不在这组凭据里，见[隐私说明](privacy-notice.md)。

## 安全与审计

密码继续使用现有 scrypt。JWT 仍只有 `sub` 和 `exp`。删除用户和停用租户的效果与密码登录相同：删除后下一次请求失效；停用租户后令牌仍可查询会话，业务接口返回 403。

日志只记录用途和结果类别，例如 `accepted`、`limited`、`timeout`、`unknown`。不记录完整手机号、验证码、密码、AccessKey、令牌或供应商原始报文。审计复用现有文件，增加操作名；目标字段只写 `尾号` 加 4 位数字。

## 升级

已有库先停止 Web 并备份，再执行：

```bash
uv run melonclaw-db-update
```

确认账本登记了 `20261126_sms_auth`、`20261126_registration_verification` 和 `20261128_sms_reset_password` 后再启动 Web。新空库执行 `uv run melonclaw-db-init`，直接创建当前结构。两条命令都不清库。服务启动不建表、不升级。

## 真实短信联调

本地测试使用供应商替身，不发送真实短信。联调前需要：

1. 开通号码认证服务的短信认证，并在赠送签名、赠送模板中确认签名和两套模板 Code。
2. 准备只含上述两个权限的 RAM 用户 AccessKey。
3. 在部署环境填写本文的配置，设置 `MELONCLAW_SMS_ENABLED=true`。需要自助注册时再打开 `MELONCLAW_REGISTRATION_ENABLED`。
4. 重启 Web。用测试号码分别走注册、密码登录、短信登录和首次绑号。

预期结果是发送接口受理、手机收到验证码、核验通过。两种登录方式进入同一账号，管理员能查到唯一一条脱敏注册记录。错误验证码、超时和 `UNKNOWN` 不进入工作台，也不产生成功记录。未获明确授权和测试号码时，不要调用真实发送接口。控制台快速测试和正式发送都会计费。

## 验证

- `tests/test_sms_provider.py`：成功响应丢弃明文验证码，`UNKNOWN`、缺字段、错误码和超时的解释，以及 SDK 请求参数。
- `tests/test_sms_auth.py`：注册后两种登录的用户一致、未注册号码不能登录、忘记密码重置及边界（模板回退、密码前置校验、跨用途、锁冲突重试、重放、未绑定/已删除拒绝、既有 JWT 与注册留痕不变）、错误码、发送超时、旧码、跨用途、频控、并发提交、老账号绑号、重复号码、删除用户、停用租户、管理员拒绝绑号，以及审计不含号码、密码和验证码。
- `tests/test_sms_postgres.py`：隔离库升级（含 `20261128_sms_reset_password`）保留原账号，重复执行无变化。覆盖失败、过期、并发、注入数据库写入失败后的回滚、找回密码写库与防重放，以及管理员查阅、普通用户拒绝、后续登录和短期请求清理后记录不变。
- `frontend/tests/account-login.test.tsx`：开关、倒计时、切换入口、忘记密码找回与预填用户 ID、更换手机号清除验证码和隐私说明草稿恢复。
- `frontend/tests/account-management.test.tsx`：管理员查看脱敏记录、按验证编号搜索和查阅已删除账号。

`tests/test_sms_postgres.py` 在未设置 `MELONCLAW_TEST_DATABASE_URL` 时跳过。跳过不算通过。
