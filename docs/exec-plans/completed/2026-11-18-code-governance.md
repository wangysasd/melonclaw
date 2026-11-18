# 代码治理

状态：已完成
开始日期：2026-11-18

## 目标

按当前代码证据删除已确认的冗余和旧别名，并把放错层或混在一起的职责挪回现有分层。完成后，`scripts/check.sh` 通过，当前聊天、知识库和 MCP 路径的测试仍通过，旧投影别名不再存在。

## 背景

- 分支 `main`，HEAD `471d401`。开始时工作区只有用户未提交的 `docs/deployment.md`、`docs/first-deployment.md`（nginx 默认站点说明）。这两处原样保留，未提交、未建分支。
- 已读 [AGENTS.md](../../../AGENTS.md)、[架构](../../ARCHITECTURE.md)、[执行计划约定](../README.md)、[技术债](../tech-debt-tracker.md) 和 [设计索引](../../design-docs/index.md)。领域契约按改动补读了配置、前端目录职责和 MCP 配置入口。
- 未读取或输出凭据。未执行清库、删表、`db-init` 或 `db-update`，也未连接当前配置指向的数据库。
- 扫描跳过 `.venv/`、`frontend/node_modules/`、`dist/`、`__pycache__`、`note/` 和外部运行时数据。未修改 `melonclaw-tutorial`。

## 扫描范围与未改区域

跟踪范围内查看了 `src/melonclaw/`（230 个 Python 文件）、`tests/`、`frontend/src/`、`frontend/tests/`、`scripts/`、`ops/`、数据库初始化与 `database/updates/`、资源模板、`pyproject.toml` 与命令入口。

| 区域 | 结果 |
|---|---|
| 包依赖 | `tests/test_architecture.py` 的禁止边在改动前后均由测试覆盖。未发现 services 写 SQL，也未发现 services/repository/core 抛 `HTTPException` |
| 私有符号 | 后端私有函数、方法和常量在 `src/`、`tests/`、`scripts/`、`ops/`、`.data/skills/shared/` 中没有“只出现一次”的定义 |
| 数据库升级 | `database/updates/` 的已登记步骤未改写、未删除。账户管理升级里的“兼容上线前 schema”是升级步骤，不是读取路径双读 |
| 当前协议 | `openai_compatible` 是现行供应商协议。`.doc`/`.xls` 的 `_validate_legacy_office` 是当前文件格式校验。聊天配置只允许 HTTP/SSE 是现行产品规则，与全量 `http/sse/stdio` 不是同一条规则 |
| Skill 模板 | `.data/skills/shared/` 不导入 `melonclaw`。教程 Skill 未改 |
| 合规页脚 | `App.tsx` 仍挂载 `AccessNoticeGate`。未删除声明组件 |
| 大文件但未拆 | `execution.py` 889 行、`attachments.py` 874 行、`resource_service.py` 867 行。流式执行和 `create_message_pair` 仍是一次执行或一个事务；ResourceService 共用同一套资源权限。`ModelProviders.tsx` 1098 行、`Composer.tsx` 926 行没有拆出第二个已验证职责 |

## 分类

### 已直接删除或收敛

| 项 | 证据 | 处理 |
|---|---|---|
| `AssistantStepProjector` | 只在 `output/assistant_steps.py` 作为 `AssistantStepAccumulator` 的别名和 `__all__` 出现。投影、测试和 `output/__init__.py` 都使用 Accumulator | 删除别名和导出 |
| `resource_service.MCP_TRANSPORTS` | 只在定义和 `__all__` 出现。真正校验在 `services/mcp.py`、API `Literal` 和数据库 CheckConstraint | 删除该常量。canonical 集合放到 `core/mcp_config.py` 的 `MCP_TRANSPORTS`，服务校验改用它。API Literal 与别名取值由测试锁定。数据库约束保持独立，不改已登记 schema |
| `mcp_snapshot_revision` | 生产缓存失效走 `repository/resource_versions.py`。该哈希只被 `tests/test_mcp_management.py` 自己调用 | 删除函数。测试保留空 allowlist 进入运行时选择的断言 |
| `_consume_subagents` 的 `parent_scope` | 两个调用点传入 scope 或 `None`，函数体不读。子作用域仍由 handle 的 path/name 和 `_parent_call_id` 构造 | 删除形参和实参 |
| `_require_provider_row` 的 `user_id` | 方法只按已解析的 role 做管理员检查，再取供应商行。调用方仍先 `await self._role(user_id)` | 删除未读形参。权限检查和调用顺序保留 |
| `tasks.now` | 与 `repository/mappers.py` 的 `_now` 同为 `datetime.now(UTC)`，且只在 `tasks.py` 内部使用 | 任务写入改调用 `_now`，求值时机仍在原语句 |
| 账本复用条件 | `create_or_get_user_interaction` 内联了与 `_is_open_ledger` 相同的 waiting 判断 | 事务内改为调用 `_is_open_ledger`。读写、归档和唯一键未动 |
| `parseResultBlock` | 生产渲染使用 `parseResultBlockDetailed`。包装只返回 `.result` | 删除包装。原有效、非法、超长和来源引号断言改为检查 `.result` |
| `MELONCLAW_HOST` / `MELONCLAW_PORT` | 只在 `api/app.py` 的 `main` 里直接读环境变量 | 解析移到 `core/config.py` 的 `web_bind()`。默认值、整数错误和 uvicorn 参数保持原样 |
| 知识库详情拼装 | `GET /api/knowledge-bases/{id}` 在路由里先后调用详情和已发布文档计数 | 这两次调用移到 `KnowledgeService.detail`。路由仍负责响应和脱敏 |
| 聊天/会话归约 | `lib/agentRun.ts` 为了消息类型依赖 `hooks/useChatStream.ts`。两个文件同时放着纯 reducer 和请求/流副作用 | 归约分别移到 `state/chatStream.ts`、`state/sessionState.ts`。Hook 和 Provider 单向依赖它们 |
| 工具名读取 | `_format_tool_summary` 复制了 `_tool_name` 的 getattr 链 | 摘要改为调用 `_tool_name` |
| 未使用导入 | `tests/test_knowledge_management.py` 顶层导入 `DatabaseSchemaError`，实际使用的是同文件后面的局部导入。这是改动前 ruff 就会报的 F401 | 删除顶层导入，保留局部导入和使用处 |

### 需确认后再动

| 项 | 为什么不动 |
|---|---|
| `load_mcp_tools` | 只有测试调用，但空配置、取消传播和脱敏重抛与 `build_mcp_tools` 不同。见 D15 |
| `session.runStatus` | 界面不读，但写入会更新 Session Context，测试锁定了这些写入。见 D14 |
| `summariseAgentTools` | 收起态工具摘要没有组件调用，计数规则仍被测试固定。见 D16 |
| 公开路由与五个 CLI | 路由函数在源码里常常只出现在装饰器处。没有把“搜索不到函数名”当成删除证据 |
| 旧审批展示、品牌图、图标名、依赖裁剪、密码命令的第二次环境加载 | 外部消费者、历史 Checkpoint 或配置来源仍未证明可以删除。沿用技术债里的边界 |
| 后端 900 行附近的执行与附件事务 | 行数是线索。没有第二个已验证职责，不拆 |

## 步骤

- [x] 记录工作区，阅读架构、执行计划约定和技术债
- [x] 扫描后端、前端、测试、脚本、数据库升级和资源模板
- [x] 记录改动前测试基线
- [x] 删除已确认的别名、未使用常量和未读参数
- [x] 收敛传输集合、时钟、账本判断和知识库详情
- [x] 把聊天与会话归约移出副作用模块
- [x] 同步架构、配置、前端目录说明和技术债
- [x] 运行 `scripts/check.sh` 并记录通过、失败、跳过和未运行

## 验收标准

- `scripts/check.sh` 退出码为 0。
- `AssistantStepProjector` 不再存在于 `src/`。
- `web_bind()` 默认返回 `127.0.0.1:8000`，非整数端口抛出 `RuntimeError`，`main` 把结果传给 uvicorn。
- 未知 MCP transport `websocket` 被 `validate_mcp_payload` 拒绝；API `Literal` 与 `MCP_TRANSPORTS` 一致。
- 知识库详情成功时仍返回 `published_document_count`，驱动异常仍是 503 且不回显异常文本。
- 聊天流、执行区、结果组件和任务投递测试保持原断言。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-18 | 不提高 900 行上限，也不拆执行事务和附件建消息事务 | 行数未超限，职责仍是一次执行或一个事务 |
| 2026-11-18 | 保留数据库 CheckConstraint 里的传输字面量 | 它是结构约束，不是运行时兼容分支；不改已登记升级 |
| 2026-11-18 | 不删除 `load_mcp_tools`、`runStatus`、`summariseAgentTools` | 分别还有不同错误语义、Context 更新或未确认的展示规则 |
| 2026-11-18 | 监听地址不放进 `Settings` | `load_settings()` 会创建数据目录；只为绑定端口不应触发它 |

## 风险与依赖

- 账本复用改走同一谓词后，隔离 PostgreSQL 上的并发/重试事务本次没有重跑。
- 没有运行真实模型、MCP、OCR 或当前业务库。
- 没有重跑前端生产构建，因此不更新 D12 的包体积数字。
- 用户已有的两份部署文档差异必须在最终 diff 里保持原样。

## 验证记录

改动前，2026-11-18：

`uv run pytest -q -rs tests`：1124 passed，123 skipped，2 xfailed，29 warnings，27 subtests passed，32.20 秒。没有失败。123 项跳过都是缺少隔离 PostgreSQL、pgvector 或未显式开启真实服务。2 个 strict xfail 是 `tests/test_review_background_stream.py` 的 `test_producer_exception_is_retrieved` 与 `test_close_before_first_iteration_closes_source_iterator`。

定向验证，2026-11-18：

- `uv run pytest -q` 覆盖 `test_web_bind.py`、`test_mcp_service.py`、`test_mcp_management.py`、`test_knowledge_management.py`、`test_user_input.py`、`test_agent_tool_assembly.py`、`test_parallel_tool_stream.py`、`test_task_configuration.py`、`test_task_reliability.py`：109 passed，22 skipped，7 warnings。跳过仍是隔离 PostgreSQL。
- 前端 eslint 与 `tsc --noEmit` 通过。修正一处：`session.tsx` 的 `dispatchSync` 需要导出 `SessionAction`。
- 受影响的 9 个前端测试文件 154 passed。antd 的 `dropdownRender`/`width` 弃用警告和 `NaN` height 警告是原有输出。

完成后，2026-11-18：

`scripts/check.sh` 退出码 0。通过项：`git diff --check`、`compileall`、pytest、ruff、`uv lock --check`、eslint、`tsc --noEmit`、vitest。前端依赖已安装，没有跳过前端。没有失败项。

随后单独汇总 pytest：1129 passed，123 skipped，2 xfailed，29 warnings，27 subtests passed，30.77 秒。比改动前多 5 项，对应 4 个监听地址测试和 1 个传输集合测试。123 项跳过与 2 个既有 strict xfail 保持不变。

未运行：当前业务库、`db-init`/`db-update`、真实模型/MCP/OCR、前端生产构建、浏览器里的完整登录和聊天。客户端状态搬迁由上述 Vitest 覆盖；本机没有已启动的 Web 进程，没有为了浏览器验收去连接业务数据库。
