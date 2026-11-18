# 技术债清单

只记录**已知且尚未解决**的问题。解决后移到「已偿还」，保留一行用于追溯。

最后核实日期：2026-11-18（本轮只复核仓库代码、架构测试与本机 `scripts/check.sh`；CI、生产状态和真实数据库仍待核实。2026-10-17 的 TRY/BLE 统计与前端包体积没有重测）。

## 待处理

| 编号 | 问题 | 影响 | 建议动作 | 依据 |
|---|---|---|---|---|
| D2 | 完整 Agent/Checkpoint/SSE 组合仍缺可重复集成验证 | 2026-11-18 改动前基线为 1124 passed、123 skipped、2 xfailed。跳过项仍要求隔离 PostgreSQL、pgvector 或显式真实服务，不能据此宣称完整执行链路已验证 | 持续运行现有隔离测试，再补完整 Agent 事件序列、Checkpoint 恢复与 SSE 投递的组合场景 | `docs/QUALITY_SCORE.md`、`tests/test_accounts_postgres.py`、`tests/test_task_postgres.py`、`tests/test_artifact_postgres.py` |
| D3 | 未启用的 TRY/BLE 扩展规则仍有告警 | 2026-10-17 审计基线为 435 条：TRY003×320、TRY301×54、BLE001×28、TRY004×19、TRY300×13、TRY203×1；常规 lint 通过，这些告警不等于独立缺陷 | 按用例审查异常类型、取消传播与有意兜底，再决定规则启用范围，不能机械改写或批量加 `noqa` | `uv run ruff check --select TRY,BLE src tests --statistics`；[审计基线](completed/2026-10-17-code-cleanup-audit.md#验证基线) |
| D5 | 构建产物 `__pycache__`、依赖目录与源码混在树里 | 检索噪声大，容易误读文件规模 | 依赖 `.gitignore` 已覆盖，检索时注意排除 | 仓库结构 |
| D6 | 大文件倾向 | 2026-11-18 已把聊天归约和会话归约拆出。仍大的文件：`useChatStream.ts` 1250 行（只剩流副作用）、`session.tsx` 891 行（Provider）、`ModelProviders.tsx` 1098 行、`Composer.tsx` 926 行、`execution.py` 889 行、`attachments.py` 874 行、`resource_service.py` 867 行。均未超过后端 900 行上限 | 只在出现第二个可独立验证的职责时再拆。`execution._stream_execution` 与 `create_message_pair` 仍是单个事务或单次流，不机械切开 | `tests/test_architecture.py`、[本次计划](completed/2026-11-18-code-governance.md) |
| D7 | 无本地可观测性栈 | agent 无法自行验证启动耗时、事件延迟等指标 | 需要时再加最小实现，不要提前建设 | `docs/QUALITY_SCORE.md` |
| D9 | 仓库不再提供自动 CI，分支保护状态待核实 | 推送和 PR 不再自动执行检查；若分支保护仍要求旧检查，可能阻止合并 | 提交前运行 `scripts/check.sh`，并核实分支保护配置 | `scripts/check.sh` |
| D10 | Shell 沿用受信任本地开发免审批例外 | `execute` 未登记在当前审批清单；`LocalShellBackend` 不是沙箱，误操作/提示注入可直达宿主机 | 先设计隔离与授权边界，再评估 `core/hitl.py` 的 `TODO(sandbox)`；旧注释配置本体已删除，不能靠取消注释恢复 | `src/melonclaw/core/hitl.py`、`docs/ARCHITECTURE.md` 第 5.1 节 |
| D11 | 长会话流持续占用业务数据库连接 | 同时执行数接近连接池上限时，普通业务查询会等待 | 记录连接池等待和锁持有时长；确认负载后优先评估独立限额锁池，再设计可保护 Checkpoint 的租约方案 | `repository/locks.py`、`database/database.py` |
| D13 | 后台 SSE 包装器两项边界缺口 | producer 异常未取回、首次迭代前关闭源的清理仍为 strict xfail；低层测试不能单独证明 ASGI 真实请求泄漏 | 保留两项测试并补独立生命周期/断线端到端证据，不靠清理测试规避 | `tests/test_review_background_stream.py` |
| D12 | 前端主入口仍较大 | 2026-10-11 构建仍有超过 500 KB 的入口/图表分块，最大 index 块约 1.56 MB（gzip 524 KB）。2026-11-18 未重跑生产构建 | 用浏览器网络记录和构建分析确定首屏依赖，再懒加载非首屏弹窗；仅配置 `manualChunks` 不减少总下载量 | `npm run build` |
| D14 | `session.runStatus` 只写不读 | 聊天流和上下文切换会写入它，测试锁定这些写入；当前组件不读取。删掉会改变 Session Context 的更新次数 | 先对照认证切换、恢复、停止和跨会话，证明没有依赖这些更新的重渲染，再迁走断言 | `frontend/src/state/sessionState.ts`、`frontend/src/hooks/useChatStream.ts` |
| D15 | `load_mcp_tools` 只有测试调用 | 生产装配走 `build_mcp_tools`，会附带目录工具；公开函数另有空配置、取消传播和脱敏重抛，两套语义不同 | 确认没有仓库外导入后，再决定保留或迁移其失败语义。不要把它当成 `build_mcp_tools` 的空壳删掉 | `src/melonclaw/tool/tools.py`、`tests/test_mcp_tools.py` |
| D16 | 收起态工具摘要没有界面调用 | `summariseAgentTools` 只被 `agent-execution.test.tsx` 使用。执行区组件不调用它 | 先确认收起态不再展示工具名摘要，再删除函数和对应断言。在此之前保留计数规则 | `frontend/src/lib/agentRun.ts` |

## 审计结束后的独立评估记录

2026-10-17 的未实施候选不是继续执行的授权。2026-11-18 重新核对后，已处理的项见[代码治理计划](completed/2026-11-18-code-governance.md)。下表只保留仍未处理的边界。

| 候选 ID | 当前记录 | 后续边界 |
|---|---|---|
| CLN-014 | 两种刷新 Hook 有重复循环，但业务语义不同 | 默认保持分离，不扩大普通会话正文轮询 |
| CLN-016、018 | 大模块职责与前端首屏分块 | 需要独立拆分方案或性能测量；同 D6/D12 |
| CLN-028、031 | `parseResultBlock` 与 `tasks.now` 已在 2026-11-18 收敛。`summariseAgentTools` 仍只被测试调用 | 见 D16；不要为了消除测试而删掉尚未确认退出的摘要规则 |
| CLN-019 | 直接依赖的外部 Skill 消费者未验证 | 不凭源码没有 import 删除依赖 |
| CLN-020、030 | 旧 action 与全局运行状态 | 先证明身份/流生命周期和渲染时序等价 |
| CLN-021、027、029 | 2026-11-18 已删除 `AssistantStepProjector` 和未接入校验的 `resource_service.MCP_TRANSPORTS`。`load_mcp_tools` 仍保留 | 见 D15 |
| CLN-022 | 旧审批展示与恢复分支 | 历史 Checkpoint/快照消费者未排除，保留 |
| CLN-023、038 | 品牌图与图标资源 | 动态名称及外部静态 URL 未排除 |
| CLN-026、032、034 | 2026-11-18 已去掉未使用的 `parent_scope` 和 `_require_provider_row` 的 `user_id`，账本复用已改走 `_is_open_ledger`。`build_custom_tools` 的 `settings` 现在读取 DeepSeek 凭据，不再是未读参数。聊天安装身份核验未改 | 隔离库上的账本并发仍未重跑；不要合并安装身份校验 |
| CLN-033 | 密码命令的第二次环境加载 | 可能改变配置来源，不能直接视作等价重复 |
| CLN-037 | 未挂载的页脚声明组件 | 文档误述已修正，产品去留尚未决定；不删除当前访问提示 |

## 已偿还

2026-10-17 文档整理：D8 的附件草稿已合并至现行附件设计和架构能力边界；OCR/对象存储等未实现扩展及旧框架行号不保留为当前功能说明。

| 日期 | 问题 | 处理方式 |
|---|---|---|
| 2026-11-18 | 旧投影别名、未接入的 MCP 传输常量、重复时钟和账本判断、HTTP 层拼装知识库详情、聊天/会话归约与副作用混在同一文件 | 见[代码治理计划](completed/2026-11-18-code-governance.md)。未改数据库，未删除仍有独立语义的公开 MCP loader |
| 2026-09-13 | `AGENTS.md` 只有规则、没有知识地图，且“提交前运行检查”没有可执行命令 | 新增知识地图与依赖方向章节，检查命令落到 `scripts/check.sh` |
| 2026-09-13 | README 混入 UI 实现细节 | 迁移到 `docs/FRONTEND.md` |
| 2026-09-13 | D1 没有 CI | 新增 `.github/workflows/check.yml`，与 `scripts/check.sh` 覆盖相同范围 |
| 2026-09-13 | 后端 lint 无配置，结果随 ruff 版本漂移 | `pyproject.toml` 增加显式 `[tool.ruff]`，钉住规则集与目标版本 |
| 2026-09-13 | D4 `pytest` 未声明为项目依赖 | 在 `[dependency-groups] dev` 中声明 `pytest`、`ruff`；`check.sh` 与 CI 统一改用 `uv run` |
| 2026-09-13 | D3 的导入顺序部分（23×I001） | `uv run ruff check --fix --select I` 整理 23 个文件，并把 `I` 纳入强制规则集 |
| 2026-11-07 | KB1 导入发布前 Path.open 参数错误 | 改用支持opener的内置open，保留UTF-8/0600；完整Processor回归及真实各格式发布/下载/替换复验通过，见[导入验收](completed/knowledge-ingestion-acceptance.md) |
| 2026-11-07 | KB2 parser 旧绑定断言不符合固定OCR契约 | 校正新库空解析引用断言，保留固定模型/供应商绑定与ACL检查；独立PostgreSQL全知识库85项通过 |
