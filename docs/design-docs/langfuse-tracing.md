# Langfuse 调用链监控

状态：已实现；2026-10-17。SDK 固定为 5.0.2，官方 Skill 已存在于本地 `.agents/skills/langfuse/`，未重复安装。

## 背景与目标

集中排查聊天和后台 Agent 的模型/工具调用、Token、耗时与失败。保留现有模型目录、审批、业务持久化和流式协议，不新增数据库对象、前端入口或 Agent 工具。

## 方案与关键选择

- 官方 `langfuse.langchain.CallbackHandler` 接入 Deep Agents/LangGraph 图调用；每次运行单独创建回调，主/子 Agent、工具、重试和摘要自然继承。
- `core/tracing.py` 为唯一运行监控入口。ChatRuntime 持有客户端，TaskScheduler 复用；最终导出由 `core/trace_exporter.py` 包装 OTLP exporter，独立 OTel provider 只导出 `langfuse-sdk` scope，避免意外采集 HTTP/数据库信息。
- 每次执行以 run_id 派生 trace ID；`tenant_id:user_id` 用于 user，`tenant_id:conversation_id` 用于 session。metadata 包含 tenant_id、run_id、model_id 和当前功能相关的项目/任务/请求/助手消息 ID；不用用户姓名或租户名称。
- 根名为 `melonclaw-chat-turn` / `melonclaw-task-run`。聊天根只记录当前用户输入和最终回复，审批/问答恢复输入使用恢复标记；恢复为新 trace，同一 session。幂等回放不创建 trace。
- 已知 `general-purpose` 子图只在监控回调中命名为 `general-purpose-agent`，使官方集成按 agent 类型显示；运行时名称和 task 工具协议保持原样，不额外插入 dispatch 观测。生命周期中间件的 `.before_agent` / `.after_agent` 仅在观测名称中改为 `.before` / `.after`，避免被官方名称启发式误分成 AGENT。

## 数据与事件流

配置 → 初始化客户端 → 根据服务端身份建立根 span → 在执行副本合并回调 → 图/模型/工具/子图产生观测 → 根记录最终输出或失败/取消/中断 → 后台批量导出。

聊天仍由原服务提交业务终态再发送 completed。追踪适配器消费原事件，显式关闭内部异步迭代器以保留锁释放；不会吞掉业务异常或取消。后台根从有效身份和模型解析后开始，覆盖历史准备、摘要与图执行；准备之前的队列扫描和模型解析不计入 trace。普通任务会话追问在消息准备阶段发生的历史摘要位于聊天 trace 建立之前，当前不采集；图运行中的压缩摘要仍随回调采集。

## 失败与安全边界

- SDK 初始化/观测创建/更新/结束故障记录固定警告，业务继续。网络导出失败可能丢 trace，业务成功不依赖上报成功。布尔配置格式错误按应用配置错误处理。
- `trace_privacy.py` 创建阶段移除任意运行对象、结构化凭据与媒体；导出阶段覆盖 input/output、metadata、model parameters 和状态文本。错误在进入 SDK 前先脱敏；最终 exporter 另外清理 OTel status description、events 和 links，并将 Resource 限定为固定 service.name=melonclaw（忽略 OTEL_RESOURCE_ATTRIBUTES 等自动检测），关闭内容时移除事件/链接正文并保留状态码。SDK 属性钩子只覆盖 attributes，不能替代这层边界。SDK/导出器诊断日志通过父 logger 的首个 handler 变换传播记录，覆盖子 logger；不打印原异常、响应正文和凭据。
- 常见 API Key/Token、密码、授权头、Cookie、带密码连接 URL、邮箱和大陆手机号按规则脱敏，已知应用凭据按字面值删除。普通业务 `data` 字段递归脱敏并保留可用结果，只有媒体 envelope 的 data 被整体屏蔽；任意对象作为字典键时不调用 str/repr。文本最大 100,000 字符、集合最多 1,000 项、深度最多 20，超界明确标记或截断。
- SDK 自动媒体上传强制关闭；文本可能包含附件派生正文和商业数据。规则不是完整 DLP：默认内容采集 true，严格保密部署应设 `MELONCLAW_LANGFUSE_CAPTURE_CONTENT=false`，只保留输入/输出占位、结构、用量、模型及白名单业务标识。此开关不影响本地消息或模型实际输入。
- SDK 自动在 scope metadata 中附加项目 Public Key 标识，这是 Langfuse 的项目路由信息；应用正文与自定义属性仍移除已知凭据，Secret Key 不进入观测。Langfuse 凭据受 Settings repr 隐藏并加入 Agent Shell 禁传清单，即使环境白名单也不能透传；这不把 LocalShellBackend 变成安全沙箱。
- 已启用运行中的 SDK 使用快照，配置修改重启生效。停服先结束执行，再在线程中 shutdown/flush 客户端与 provider；进程强杀不能保证排空。后台总时限、用户取消和服务关闭分别记录 execution_timeout、cancelled、service_shutdown，根状态与任务终态保持一致；已显式设置终态时不被协程取消信号再次覆盖。

## 运行与预期结果

执行 `uv sync --locked`。在项目环境配置中填写区域地址与项目凭据、开启 `LANGFUSE_TRACING_ENABLED=true`，按部署设置 `LANGFUSE_TRACING_ENVIRONMENT`，然后 `uv run melonclaw-web` 或重启已有服务。不需要 db-init/db-update 或 MCP。

发送普通聊天，在 Traces 看到 `melonclaw-chat-turn`，generation 有真实模型和供应商返回的用量；工具和子 Agent 按父链嵌套。在定时任务触发后看到 `melonclaw-task-run`，同一个固定会话下的聊天和后台运行进入同一 session。供应商未返回的 Token 维度不编造；自定义模型价格可能需要在 Langfuse 配置。

## 验证记录

- 真实 SDK + 内存导出器测试：开关、配置凭据隐藏、并发 handler 隔离/ContextVar 复位、输入副本不变、脱敏、内容关闭、错误 OTel status、安全降级、聊天终态输出与迭代器收尾。
- 真实 Deep Agents 后台图 + 可控模型：主 Agent 委派子 Agent，子 Agent 调用搜索，4 次 generation 均归入同一 trace，搜索位于子 Agent 下，子 Agent 显示为 AGENT。
- 真实数据库模型 `deepseek-flash`：通过正常 ChatService 准备/执行完成一次普通聊天，复验产生 38 个流事件并成功落库；另运行一次后台执行核心成功。未创建定时任务或启动第二个调度器。验收会话为「Langfuse 接入验证」。通过 CLI 的 observations 接口回读日本区 trace，验证根输入/最终输出、tenant/run/model 标识、同会话 session、真实模型与用量；修正中间件类型后重新执行并回读。
  - [普通聊天验证](https://jp.cloud.langfuse.com/project/cmvbuua9e0007ad0iei5o4itu/traces/d1788f67c582ba923bb21991fe9feadd)：17 个观测。
  - [后台执行验证](https://jp.cloud.langfuse.com/project/cmvbuua9e0007ad0iei5o4itu/traces/1b637ec9afd1de05f62f99bb5d2d0a24)：10 个观测。
  - 供应商的缓存输入、推理输出分别计量，不通过普通 input/output 相加猜测 total。
- 全量检查结果在[执行记录](../exec-plans/completed/langfuse-tracing.md)中维护。

## 全面回归（2026-10-17）

新增全面验收使用真实 SDK、内存 exporter、随机 PostgreSQL schema、真实 Checkpointer/Memory、实际 APScheduler 和独立子进程云端验证。50 项自动专项测试通过；3 项显式云端验证覆盖真实数据库模型的图调用、内容关闭和完全关闭。普通全量检查跳过这 3 项外部测试，不自动产生模型费用。详情与修复证据见[全面验收记录](../exec-plans/completed/langfuse-comprehensive-validation.md)。

可重复执行：

```bash
uv run pytest -q tests/test_langfuse_tracing.py tests/test_langfuse_privacy.py tests/test_langfuse_lifecycle.py tests/test_langfuse_chat_postgres.py
MELONCLAW_TEST_LANGFUSE_LIVE=true uv run pytest -q -s tests/test_langfuse_live.py
```

数据库专项需在本地环境设置 `MELONCLAW_TEST_DATABASE_URL`；只创建并清理随机测试 schema。真实测试需已有 Langfuse 项目凭据和管理员可用数据库模型，并会产生一次模型调用及监控记录；每种策略在独立进程运行，不改用户 `.env` 或现有业务会话。

## 官方参考

[LangChain 集成](https://langfuse.com/docs/integrations/langchain/tracing)、[最佳实践](https://langfuse.com/docs/observability/best-practices)、[导出脱敏](https://langfuse.com/docs/observability/features/masking)。以实际安装 SDK 源码核实 callback/context 与禁用媒体行为。
