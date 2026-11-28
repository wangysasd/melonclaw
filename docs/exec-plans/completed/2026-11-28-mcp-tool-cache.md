# MCP 工具发现跨会话缓存

状态：已完成
开始日期：2026-11-28
完成日期：2026-11-28

## 目标

新会话首条消息不再每次重新连接全部 MCP 服务：相同有效 MCP 配置在进程内复用已发现的工具，Agent 冷构建从约 2 秒降到约 0.5 秒以内；确定性的 MCP 配置错误不再阻止 Agent 缓存。

## 背景

2026-11-28 实测（17 个共享 MCP、28 个 Skill、DeepSeek-V4.1-Flash）：

- Agent 冷构建 1.4–1.9 秒，其中 `build_agent_tools` 的 MCP 发现占 1.1–1.3 秒，`create_deep_agent` 仅 0.2–0.3 秒；历史记录准备耗时最高 4.6 秒。
- 六因子缓存键含会话工作区，每个新会话必然重新发现 MCP。
- 任一 MCP 失败（包括 `${VAR}` 无法解析这类确定性配置错误）即 `melonclaw_mcp_failed=True`，Agent 永不缓存，同会话每条消息也重新构建。停用该 MCP 后同会话热命中恢复为 0 ms。

相关契约：[环境变量与聊天准备](../../design-docs/environment-and-preparation.md)、[MCP 两层配置](../../design-docs/mcp-two-layer.md)。

## 方案

- `tool/tools.py`：`discover_mcp_tools` 返回 `McpDiscoveryResult`（工具、清单、失败摘要、临时失败集合）；配置解析失败和工具定义无效为确定性失败，连接/超时/协议/HTTP 错误为临时失败。`build_mcp_tools`/`build_agent_tools` 接受预先发现的结果；清单工具元数据增加 `mcp_transient_failures`。
- `core/agent.py`、`core/task_agent.py`：`melonclaw_mcp_failed` 只表示存在临时失败。
- `services/mcp_tool_cache.py`：进程内缓存。键 = 已解析服务配置与白名单的 SHA-256 指纹 + 全局资源版本；成功 10 分钟、含临时失败 30 秒，LRU 64 项，同键单次发现。只保存在内存，不写日志。
- `services/runtime.py`：缓存未命中构建 Agent 时先从工具缓存取结果再组装；`close()` 清空。
- 后台任务 Agent 本期不接入缓存。

## 步骤

- [x] 发现结果结构化并区分失败类型（`tool/tools.py`）
- [x] Agent 装配接收预发现结果，调整失败缓存语义（`core/agent.py`、`core/task_agent.py`）
- [x] 新增 MCP 工具缓存并接入 runtime（`services/mcp_tool_cache.py`、`services/runtime.py`）
- [x] 测试：缓存命中/指纹失效/全局版本失效/TTL/临时失败短期缓存/并发单次发现/取消；Agent 缓存的确定性失败可缓存
- [x] 同步设计文档与架构文档
- [x] `scripts/check.sh` 与本机实测

## 验收标准

- `scripts/check.sh` 通过（区分跳过项）。
- 相同 MCP 配置下第二个新会话的 `agent_for_conversation` 不调用 MCP 发现，实测冷构建 < 0.6 秒。
- MCP 配置错误时同会话第二次调用命中 Agent 缓存；连接失败时不缓存 Agent。
- 修改 MCP 配置、白名单、启停或任一共享资源后，下一次构建重新发现。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-28 | 键用已解析配置指纹而非用户 ID | 配置（含凭据、白名单、启停）变化自动换键；相同共享配置跨用户复用，凭据与权限完全一致，不扩大能力 |
| 2026-11-28 | 键附带全局资源版本 | 管理员保存任一共享资源即可强制重新发现远端新增工具，无需新增管理入口 |
| 2026-11-28 | HTTP 4xx 也按临时失败处理 | 远端凭据可能在系统外轮换，保守地短期缓存并重试 |
| 2026-11-28 | TTL 用模块常量，不新增环境变量 | 避免扩大部署配置面；需要调整时再提升为配置 |

## 风险与依赖

- 远端 MCP 自行增删工具时，最长 10 分钟后或重启/保存共享资源后才可见。
- 依赖 langchain-mcp-adapters 工具在无 session 时每次调用新建连接（当前版本如此），工具对象无会话状态，可跨 Agent 共享。

## 验证记录（2026-11-28）

### 工程测试
- 定向 pytest（`tests/test_mcp_tools.py`、`tests/test_mcp_tool_cache.py`、`tests/test_agent_cache.py`、`tests/test_agent_tool_assembly.py`、`tests/test_task_resources.py`、`tests/test_architecture.py`）：**55 passed**。
- `scripts/check.sh`：
  - 通过：`git diff --check`、`compileall`、`ruff`、`uv lock --check`、前端 `eslint`、`tsc --noEmit`；后端 pytest 1198 passed（含 `test_architecture.py` 与文档链接校验）、2 xfailed；前端 vitest 62/63 文件通过（496/497 用例）。
  - 跳过：后端 134 项因未配置隔离测试数据库或外部服务跳过。
  - 既有/无关失败：后端 7 项（`test_account_api.py` 5 项、`test_sms_auth.py` 2 项，为本机 `.env` 开启短信/注册开关导致的既有失败）；前端 `tests/taskCenter.test.tsx` 1 项在并行执行下 5000ms 超时（本次未改前端）。

### 本机真实 MCP 实测（16 个已启用共享 MCP、28 个 Skill、本机 PostgreSQL）
- 进程内首个新会话（MCP 缓存冷）：`agent_for_conversation` 1740 ms（含约 1.2s MCP 首次发现）。
- 同会话第二次（Agent 缓存热命中）：**0 ms**。
- 另一个新会话（命中跨会话 MCP 工具缓存）：`build_agent_tools` **1 ms**（优化前 1856 ms），`agent_for_conversation` 合计 **382 ms**（优化前 2241 ms）。
