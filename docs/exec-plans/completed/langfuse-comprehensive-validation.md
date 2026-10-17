# Langfuse 全面验证与修复

状态：已完成
开始日期：2026-10-17

## 目标

用可复现用例验证开关、真实 SDK、业务执行、隐私、故障、并发、恢复与后台任务；发现问题直接修复并复验。

## 步骤

- [x] 阅读已有接入和官方最佳实践，派发 Luna6/high 独立审查与隐私测试。
- [x] 补充生命周期/并发/审批恢复/失败/关闭用例，修复可复现问题。
- [x] 在随机 PostgreSQL schema 中测试聊天、任务调度和终态一致性，不触碰现有业务记录。
- [x] 执行真实 Langfuse 上报与 CLI 回读；核实开关与内容策略。
- [x] 运行 scripts/check.sh，记录通过、跳过和验证边界，更新设计和质量文档。

## 验收标准

新增专项用例和全量检查通过；同会话跨轮恢复可关联，不同用户/租户互不混串；关闭不创建客户端或上报；内容关闭不泄漏正文，保留模型/用量；错误、取消与后台终态一致；所有测试 schema 清理完成。真实外部验证只上传受控测试内容，不打印凭据。

## 风险与依赖

依赖本地 PostgreSQL 创建随机 schema 的权限及已有日本区 Langfuse 项目。数据库测试只创建/删除本轮测试 schema；不启动第二个生产执行器、不重启现有 Web、不修改用户 .env 开关。

## 修复记录

| 问题 | 复现与修复 | 验证 |
|---|---|---|
| 内容关闭仍泄漏模型参数 | 实际属性名为 `.model.parameters`，修正匹配 | 真实 SDK generation 的正文/参数消失，model/usage 保留 |
| OTel status/events 绕过属性脱敏 | 新增最终 PrivacySpanExporter，只复制安全状态/事件/links；关闭内容时移除事件/链接正文 | 真实内存导出与云端关闭正文验证 |
| Resource 自动检测绕过脱敏 | 固定 provider 与最终 Resource 为 service.name=melonclaw，不采集环境自动检测字段 | 开启/关闭正文两种模式的资源秘密都不外发 |
| SDK 子 logger 传播绕过父 logger filter | 父 logger 首个 handler 先变换传播记录 | 3 类子 logger 的合成秘密不出现在捕获日志 |
| 任意对象字典键触发 repr | 只接受内置标量键，未知键和值省略 | 真实 SDK 输入中的 repr 调用次数为零 |
| 普通业务 data 被整体屏蔽 | 区分普通结果与媒体 envelope | 保留价格值，仍隐藏 API Key 和媒体原文 |
| 后台超时/停服 trace 误记 cancelled | 基于总时限及真实用户取消区分终态，已设置状态不被上下文取消覆盖，增加 error_code | 构建超时、执行超时、停服均与数据库 failed 一致；用户取消为 cancelled |
| 子进程 Web 测试替身未注入新资源 | 测试 fixture 明确提供 runtime.tracing=None，无业务兼容分支 | 完整 Web 启动、停止、崩溃重启测试通过 |

## 测试矩阵

50 项自动专项：12 项原始追踪测试、14 项隐私/故障测试、22 项生命周期/并发/调度测试、2 项真实 PostgreSQL 聊天测试。覆盖 20 路跨用户/租户并发、实际审批恢复、多轮 session 与幂等回放、SSE/数据库终态一致、关闭后聊天可用、超时、取消、停服、历史摘要继承和 APScheduler 真正触发。数据库仅使用随机测试 schema，实际 Checkpointer/Memory Store 也在同一隔离 schema。

真实云端测试通过 3 项：开启模式调用数据库解析的真实模型并回读 trace；正文关闭模式验证云端内容消失且模型/用量保留；完全关闭模式验证不创建客户端和回调但模型流程可运行。每个模式独立子进程，环境为 test，不修改 .env，不创建生产任务或现有会话。

## 可重复运行

```bash
uv run pytest -q tests/test_langfuse_tracing.py tests/test_langfuse_privacy.py tests/test_langfuse_lifecycle.py tests/test_langfuse_chat_postgres.py
MELONCLAW_TEST_LANGFUSE_LIVE=true uv run pytest -q -s tests/test_langfuse_live.py
scripts/check.sh
```

数据库测试依赖环境中的 `MELONCLAW_TEST_DATABASE_URL`，只创建/删除随机 schema；缺失时显式跳过。真实测试另外依赖 Langfuse 项目凭据和管理员可用模型，首次开启模式产生一次真实模型调用。正常全量检查不访问云端；3 项外部测试默认跳过。既有 2 项后台 SSE 生命周期 xfail 属于原有已知缺口，不计为通过。

## 最终验证结果

- `scripts/check.sh`（注入随机 schema 测试连接，默认禁用真实云端访问）：全部通过。JUnit 报告 805 个用例项（含 27 个 subtests），0 failures / 0 errors；773 项普通测试通过、27 子用例通过、3 项云端验收默认跳过、2 项原有 strict xfail。编译、ruff、锁文件、文档链接、前端 lint/typecheck/vitest、diff 空白检查通过。
- Langfuse 自动专项 50 passed；显式云端专项 3 passed。真实开启/内容关闭复验通过后回读记录：
  - [开启追踪、真实模型](https://jp.cloud.langfuse.com/project/cmvbuua9e0007ad0iei5o4itu/traces/d9703a2c24ce7179dc96cb7820584aa3)
  - [关闭正文、保留模型与用量](https://jp.cloud.langfuse.com/project/cmvbuua9e0007ad0iei5o4itu/traces/a0dea2835b40fb73487f7e4b504104af)
- 清理检查：随机任务/聊天测试 schema 剩余 0。没有数据库结构升级、清理现有业务数据或重启当前 Web。用户原有 `.env` 开关保留；需要重启 Web 加载修复。
- Luna6/high 协助独立审查和新增隐私/聊天案例，实际运行、失败复现、实现修复和全量验收由主代理完成。
