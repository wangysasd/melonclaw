# 启动预热与调模型前开销优化

状态：已完成
开始日期：2026-11-28
完成日期：2026-11-28

## 目标

消灭 Web 重启后首条消息等待共享 MCP 发现的 ~1.3 秒冷启动耗时，并消除进图后到发出模型请求前的冗余开销（惰性 QuickJS 初始化、跳过未调用 `eval` 时的 1.31 MB 快照与千万次遍历、缓存不可变 Skill 快照元数据、并发读取 Memory 与准备检查），将首轮与后续轮次的进图到 `LLM_START` 延迟均压至 ~80 ms 以内。

## 背景

2026-11-28 实测定位到四个主要延迟来源：

1. Web 重启后 `mcp_tool_cache` 为空，首条消息仍需等待 ~1.2–1.9 秒发现 16 个共享 MCP。
2. `CodeInterpreterMiddleware._prepare_for_call` 在首次模型调用前即启动 QuickJS REPL（~156 ms），即使整轮未调用 `eval`，`aafter_agent` 仍把 1.31 MB 堆快照写入 `state["_quickjs_snapshot_payload"]: bytes`；随后 LangGraph `StreamMessagesHandler.on_chain_start` 将 `bytes` 当作 `Sequence` 逐字节遍历（每节点 131 万次，首轮之后每轮 8+ 节点累计 1050 万次 `isinstance`），导致 Turn 2 到达 `LLM_START` 耗时 369 ms、Turn 3 达 490 ms。
3. `SkillRefreshMiddleware` 每轮重读 28 个不可变快照 `SKILL.md` 并重新解析 YAML（10–15 ms/轮）。
4. `MemoryService.load_prompt` 串行校验 3 次用户上下文并串行查 3 个 scope（6 次串行 DB 往返）；`ExecutionService.prepare_message` 串行检查 `aget_pending_interaction` 与 `get_incomplete_assistant`。

## 步骤

- [x] `services/runtime.py` + `api/lifespan.py`：新增 `warmup_shared_mcp_cache()` 并在启动后后台预热、退出时安全取消。
- [x] `core/interpreter.py`：新增 `LazyCodeInterpreterMiddleware`，推迟 QuickJS 实例化与 PTC 工具注入到真正调用 `eval` 时，保留 `mode="thread"` 跨轮持久化语义。
- [x] `middleware/skill_refresh.py` + `core/agent.py` + `services/runtime.py`：不可变 Skill 快照启用实例内元数据缓存，消除每轮重复扫盘。
- [x] `memory/service.py` + `services/execution.py`：`load_prompt` 合并一次鉴权并并发查询三作用域；`prepare_message` 并发读取待交互与未完成消息。
- [x] 补充单元测试、同步设计文档、运行 `scripts/check.sh backend` 与本机实测对比。

## 验收标准

- `scripts/check.sh backend` 工程检查、lint、架构依赖与本次新增测试通过。
- Web 启动后后台完成共享 MCP 预热，重启后的首个新会话不再等待远端 MCP 发现。
- 未调用 `eval` 的对话：Turn 1/2/3 均不写入 `_quickjs_snapshot_payload`，进图到 `LLM_START` 延迟均 < 80 ms（优化前 Turn 1 227 ms、Turn 2 369 ms、Turn 3 490 ms）。
- 实际调用 `eval`（含 PTC 工具和跨轮变量持久化）功能保持正常。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-28 | 通过子类 `LazyCodeInterpreterMiddleware` 惰性初始化 QuickJS，不改 `REPLState` 字段类型 | 保留上游 `DeltaChannel(replay_snapshot_chain)` 与 `mode="thread"` 兼容性，同时在未用 `eval` 的 95%+ 轮次彻底消除 156ms 启动、1.31MB 快照与 LangGraph `bytes` 逐字节遍历 |
| 2026-11-28 | `SkillRefreshMiddleware` 通过 `cache_snapshot=True` 显式开启缓存，默认保持 `False` | `runtime.py` 传入的是内容寻址不可变快照目录，而单元测试中直接挂载可变目录仍可实时刷新 |
| 2026-11-28 | 启动预热作为独立后台任务运行，不阻塞 `lifespan` 就绪 | 避免某远端 MCP 超时拖慢整个 Web 服务启动；若预热期间收到请求，由 `McpToolCache` 自动合并为单次发现 |

## 风险与依赖

- 若某会话实际调用了 `eval`（使用 `mode="thread"`），该会话后续轮次仍会有 QuickJS 快照（上游 LangGraph `StreamMessagesHandler` 需等上游修复 `bytes` 过滤）。
- 预热仅覆盖全局共享 MCP（无个人覆盖的配置）；有个人 MCP 的用户仍按原有缓存规则在首次构建时发现并缓存。

## 验证记录（2026-11-28）

### 工程测试
- 定向 pytest（`test_interpreter_lazy.py`、`test_skill_refresh.py`、`test_agent_cache.py`、`test_mcp_tool_cache.py`、`test_mcp_tools.py`、`test_architecture.py`）：**36 passed**；`ruff check src tests` 通过。
- `scripts/check.sh backend`：
  - 通过：`git diff --check`、`compileall`、`ruff`、`uv lock --check`；pytest **1201 passed**（含架构依赖与文档链接校验）、2 xfailed。
  - 跳过：134 项因未设置隔离测试数据库或外部服务环境变量跳过。
  - 既有/无关失败：8 项（`test_account_api.py` 5 项、`test_sms_auth.py` 2 项为本机 `.env` 开启短信/注册开关导致的既有失败；`test_operator_contact.py` 1 项为工作区既有未提交隐私页改动相关，与本次无关）。
  - 前端：未改动，本次未运行前端套件。

### 本机实测对比（16 个启用共享 MCP、28 个 Skill、本机 PostgreSQL、DeepSeek-V4.1-Flash）
- 启动后台预热共享 MCP：1142 ms（后台异步执行，不阻塞服务就绪）；预热完成后重启首个新会话 `agent_for_conversation`：**704 ms**（优化前 ~1740–2241 ms）。
- 进图到 `LLM_START`：
  - Turn 1：227.0 ms → **67.9 ms**
  - Turn 2：369.1 ms → **24.6 ms**（首 token 413.8 ms，总耗时 442.8 ms）
  - Turn 3：490.1 ms → **30.8 ms**
- 未调用 `eval` 时的 `_quickjs_snapshot_payload` 长度：1,310,772 bytes → **0 bytes**。
