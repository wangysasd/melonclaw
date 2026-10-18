# 环境变量与准备缓存补充测试

状态：已完成
开始日期：2026-10-18

## 目标

为环境管理、聊天 HITL、资源缓存和请求准备新增有实际判定能力的测试，复现缺陷后做最小修复，保持既定权限与 MCP 规则。

## 背景

能力与边界见[环境变量与聊天准备缓存](../../design-docs/environment-and-preparation.md)。用户要求构建案例并授权 Luna/high 子智能体补充测试和优化；使用当前可用 `gpt-6-luna`、high，分别覆盖环境服务、缓存准备、前端，主智能体负责 HTTP 集成和最终验证。

## 步骤

- [x] 阅读现有设计与回归，按文件范围分配测试任务。
- [x] 编写 API 权限、验证脱敏、版本冲突与完整生命周期测试。
- [x] 补充环境服务／HITL、缓存与前端缺口，复现后修复。
- [x] 在临时 PostgreSQL 随机 schema 执行数据库与完整项目检查。
- [x] 汇总案例、真实发现、结果及验证边界，回填文档。

## 验收标准

- 管理 HTTP 接口拒绝未登录、伪造用户／scope／owner、非法值，响应不包含凭据。
- 共享／个人覆盖回退、显式空值、CAS、聊天审批绑定与拒重有可运行回归。
- 缓存热命中不新增资源 IO；资源更新、并发与请求权限重新校验不被优化绕过。
- `scripts/check.sh` 在显式临时数据库连接下通过；真实业务配置、服务和外部模型不被测试更改。

## 风险与依赖

测试只用合成数据；PostgreSQL 使用独立临时实例和随机 schema，完成后停止。付费模型、外部 MCP、生产服务与 Linux 部署不在本轮范围。不得重新引入一次性导入脚本或 `.env` 业务兜底。

## 案例矩阵

| 范围 | 可判定场景 | 位置 |
|---|---|---|
| HTTP 资源生命周期 | 管理员共享、个人空值覆盖、编辑未提交值保留、启停／删除回退、其他用户和普通用户编辑共享均拒绝、响应不返回值 | `tests/test_environment_api.py` |
| HTTP 身份与输入 | 未登录、伪造 user_id／头部／scope／owner、跨站来源、租户停用拒绝；严格布尔、null、保留名、字符／字节超限、NUL、非法 UTF-8 安全报错 | `tests/test_environment_api.py` |
| 同时更新 | 两个相同 expected_version 请求仅一方成功，另一方 409，保存值和版本属于胜出请求 | `tests/test_environment_api.py` |
| 配额与事务 | 单值 8192 UTF-8 字节、作用域 64 KiB、200 项的精确边界，超限不新增、不修改原值／版本 | `tests/test_environment_postgres.py` |
| 审批绑定与拒重 | 用户／会话／request_id／角色变化拒绝旧审批；拒绝／过期清空暂存；30 天回执清理保留 committed 墓碑并拒重 | `tests/test_environment_postgres.py` |
| 完整聊天审批 | 管理员／普通用户各自 approve、reject：原文输入先脱敏，真实 PostgreSQL Checkpoint 中断与恢复，确认前无生效，拒绝擦除暂存，消息／Checkpoint／SSE 无值，同 request 重放不重复落库 | `tests/test_environment_execution_postgres.py` |
| Shell 实际子进程 | 不透传宿主业务及平台凭据，显式数据库值／空值可用，HOME／PATH／加载器变量不可覆盖，旧实例保留快照，新实例使用新值 | `tests/test_environment_backend.py` |
| 缓存隔离与失败 | 六因子分别隔离，恢复默认模型策略独立隔离；MCP 失败不缓存；构建异常／取消释放锁；资源写入推进对应全局／用户版本 | `tests/test_agent_cache.py`、`tests/test_resource_versions.py` |
| 准备身份 | 同次准备使用可信用户上下文，后续请求重新解析身份和归属，会话换所有者后拒绝 | `tests/test_execution_prepare.py` |
| 前端操作 | 只写值、创建／替换空值、只改说明不提交隐藏 value、版本冲突不假成功、管理员／个人说明、删除携带 CAS，审批不显示意外 value | `frontend/tests/environment.test.tsx` |
| 前端失败回填 | 请求包含用户真实输入，乐观消息和失败回填均脱敏 | `frontend/tests/chat-stream.test.tsx` |

## 已复现并修复

未配对 Unicode 代理项可经 JSON 转义抵达服务端；原管理值校验和聊天草稿哈希直接编码而抛出 UnicodeEncodeError，说明字段还会绕过校验进入数据库。新增三项回归先失败，再于 `services/environment.py` 对值和说明显式拒绝不可 UTF-8 编码文本，`services/environment_chat.py` 在哈希前返回固定安全错误。合法文本、字节额度与权限规则不变，不新增查询或缓存因子。

前端失败回填与缓存新增回归均通过现有实现，没有为测试调整已正确的生产代码。文档核对同时修正架构中旧的 `.env` 透传描述与审批清单说明。

## 验证记录

- 本轮新增后端测试项 29 项（HTTP 13、Shell 2、UTF-8 3、缓存／准备 7、完整聊天 4）、前端 8 项，共 37 项；同时扩展原有 PostgreSQL 配额与审批矩阵。
- 后端八组专项 `67 passed`；真实 PostgreSQL 聊天全链路四项 `4 passed`，合计 71 项通过。前端环境表格 11 项通过，聊天流新增脱敏回归也已通过。
- 显式 `MELONCLAW_TEST_DATABASE_URL` 指向本轮独立临时 PostgreSQL，`MELONCLAW_TEST_LANGFUSE_LIVE=false` 禁止云端验证；`scripts/check.sh` 全部通过，含后端编译、pytest、Ruff、锁文件，前端 lint、类型检查、Vitest 和文档链接。
- 全量后端 JUnit 记录 911 个通过结果（包括子用例）、3 个显式云端用例跳过、2 个既有后台 SSE strict xfail；无新增失败或跳过数据库验证。
- 使用实际 HTTP／Cookie、PostgreSQL、Deep Agents 图、Checkpoint 和 Shell；模型回复为受控合成序列。没有访问外部模型／MCP，不验证自然语言理解质量、云端追踪、Linux 部署或 Shell 安全沙箱。
- 临时 PostgreSQL 完成检查后停止并清理；真实业务数据库、已迁移业务变量和既有服务未被本轮修改。
