# Agent 运行控制与用量观测

状态：已实现
日期：2026-10-04

## 背景与目标

长会话需要提前压缩上下文；复杂任务需要任务清单；异常重试、调用循环和隐藏模型调用需要明确预算与观测。沿用 Deep Agents 官方组件，不增加模型配置来源或数据库表。

## 方案与配置

`core/config.py` 是唯一配置入口，`.env.example` 与本地 `.env` 列出 `MELONCLAW_AGENT_*` 参数。模型名称、连接和凭据仍只从数据库解析；窗口由数据库 `model_configs.context_window` 提供，默认 1,000,000 tokens（十进制 1M），模型管理 UI 和创建/编辑 API 可修改。移除全局窗口环境变量；管理员修改内置模型，普通用户修改自己的个人模型。

| 参数后缀 | 默认值 | 含义 |
|---|---|---|
| OUTPUT_RESERVE | 4096 | 输入预算之外预留的输出 tokens，同时限制请求输出 |
| SUMMARY_TRIGGER_RATIO | 0.8 | 触发阈值比例：`model.context_window*ratio` |
| SUMMARY_KEEP_TOKENS | 4096 | 摘要时保留的近期消息 token 预算 |
| MODEL_CALL_LIMIT | 30 | 每任务、每个 Agent 的成功模型步骤上限 |
| TOOL_CALL_LIMIT | 100 | 每任务、每个 Agent 的工具调用预算 |
| RETRY_MAX_RETRIES | 2 | 初次请求以外的重试次数 |
| RETRY_INITIAL_DELAY | 1 | 初始退避秒数 |
| RETRY_MAX_DELAY | 10 | 最大退避秒数 |
| USAGE_ENABLED | true | 采集模型用量与上下文估算 |
| TODO_ENABLED | true | 注入官方 write_todos |

默认窗口 1,000,000 对应 800,000 tokens 摘要阈值；128,000 对应 102,400。数据库列非空且有正值约束，API/服务校验正整数及 Integer 范围。保留预算必须小于当前模型阈值，阈值加输出预留不能超过窗口；Agent 组装前校验，错误配置在工具加载前拒绝。窗口进入 ResolvedModel、公开模型信息和请求 profile，模型编辑按现有版本递增规则使 Agent 缓存失效。

## 组装与数据流

`core/agent_controls.py` 对主 Agent 和明确配置的通用子 Agent 分别创建官方摘要、调用限额、重试和 Todo 实例。`middleware/summarization.py` 在官方摘要算法前复用 `ToolPoolMiddleware.filter_request`，按 Checkpoint 中的工具池过滤目录；计数只包含当前可见工具，隐藏大目录不会提前触发摘要。该扩展沿用官方名称，原位替换框架默认摘要实例；不叠加第二套，也不靠调整传入列表顺序改变官方插槽位置。可见大工具和长历史仍按阈值压缩。摘要模型打 `nostream` 标签隐藏正文，执行 callback 仍记录用量。

Todo 开启时使用官方 TodoListMiddleware；关闭时以 `middleware/todo.py` 的 DisabledTodoMiddleware 按相同名称替换该插槽，避免 Codex 等模型 profile 再次注入 write_todos。关闭实例没有工具或提示词，保留官方清单状态定义并在每次执行开始清空旧清单；同一任务 ID 或审批恢复也不保留停用的清单。主／子 Agent 都通过同一组装入口执行此规则。启用 Todo 时仍由任务预算中间件只在新用户消息清理清单，审批恢复保留进度。

`TaskBudgetMiddleware` 在新用户消息进入图时清零官方 thread 计数和旧 Todo；状态是 private，父子不复制。业务上下文 `user_message_id` 是任务边界，审批恢复沿用该 ID，独立图调用使用 HumanMessage ID。限额采用 error 行为，超额并行工具批次在工具执行前整体拒绝；执行保存 failed，错误码 `agent_call_limit_exceeded`。不同子 Agent 分别计数，**不是整个任务跨所有 Agent 的全局金额或请求次数上限**。模型限额不把摘要、工具选择和失败重试当成功模型步骤；重试另有有限预算，用量账本记录每次真实模型尝试。

`ModelRetryMiddleware` 只重试网络/超时、429 和 500/502/503/504，使用指数退避与抖动；鉴权、参数、上下文超限、工具副作用不会重试。供应商 SDK 重试设为 0。`ProviderChatOpenAI` 标记任何已输出的正文、推理或工具参数片段，后续中断不重试，避免拼接重复回答。`ContextOverflowError` 在未输出时原样传给官方摘要兜底。内部摘要与选择器直接调用模型，不经过主模型重试 middleware，也没有 SDK 隐性重试。

`UsageObservationMiddleware` 在模型请求包装链内侧估算当前出站上下文（system、messages、可见工具 schema），发送 `context_usage`。摘要触发沿用官方 token counter 与历史消息策略，计数包含摘要插槽处的系统提示与可见工具；附件 hydration 和其他内层中间件仍可能补充请求内容，所以显示估算会与触发计数不同，不能视作精确剩余容量。附件图片/文件估算也不能代替供应商实际计费。

`output/events.py` 为每次执行创建独立 `core/model_usage.py` callback，继承到 selector、summary 与子图。发出 `model_usage`，包含调用 ID、用途、数据库模型 ID、状态、输入/输出 tokens、供应商报告的缓存读取 tokens；不保存提示词、模型响应、凭据或费用推测。失败尝试单独记录，供应商未报告时为 null；取消时未结束的记录保存为 unknown。

`ExecutionTrace` 按调用 ID 合并开始/结束，按作用域保留最后一个上下文估算。事件进入既有助手消息 `display_metadata.events`，正常完成、失败、取消、审批暂停均走现有持久化收尾；审批恢复在旧账本上追加。Web 只在已结束的回复末尾显示一行已报告输入／输出合计，包含主模型、工具选择、摘要与子 Agent，按调用 ID 去重；完全未报告时不显示，部分上报标明“已报告”，真实零值保留。请求次数、分类明细和上下文估算保留在事件中，不进入聊天界面，也不产生空的执行展开区。历史载入继续复用相同事件。Todo 沿已有任务清单展示，不强制简单问答调用工具。

## 失败与安全边界

不新增工具副作用；write_todos 只更新图状态，不进入逐次审批，也不加入 PTC。文件写入、Shell、外部写操作继续走现有权限/HITL。官方摘要历史 offload 是受控框架操作，沿既有 backend 记录历史，并不把用户工具写权限扩大。

输入很大、单条消息不可裁剪或估算低于服务端计数时，摘要仍可能无法满足窗口；最终错误会结束执行，不无限重试。摘要失败/历史 offload 失败沿官方行为处理，不能保证恢复所有媒体。当前 LocalShellBackend 仍不是安全沙箱。

## 运行与预期结果

调整 `.env` 后执行 `scripts/restart.sh` 让缓存的 Agent 重建；此次增加 model_configs.context_window 列，旧数据库需清空重建后执行 `uv run melonclaw-db-init`；不维护迁移或旧数据兼容。窗口修改后在下次 Agent 组装时生效，已开始的模型调用不会中途修改参数。复杂任务可产生 write_todos 清单；已结束回复的末尾可看到输入／输出 token，聊天执行区不展示用量明细。普通问答不会因为观测、限额或重试配置额外发起 LLM 请求；实际摘要、Todo 与故障重试会增加耗时。

## 验证记录

离线真实图与 HTTP mock 覆盖审批恢复和新消息重置、超额并行批次不执行、瞬时错误重试、部分输出禁止重试、ContextOverflowError 保留、摘要隐藏与用量记录、真实官方选择器 token 上报、子 Agent 分组、历史去重与未知用量；前端验证账本归并与未报告语义。补充实际 `build_research_agent` 装配回归，覆盖主／子 Agent 隐藏大目录不摘要、可见大目录与长历史正常摘要，以及 Codex profile 下 Todo 启停、真实工具执行和同任务旧清单清理。`scripts/check.sh` 全部通过。真实供应商、PostgreSQL 和 MCP 的计费数据需运行环境验证；不宣称离线验证能确认这些外部行为。

`tests/test_middleware_behavior.py` 增加 13 个行为案例：真实 HITL 批准 / 拒绝与任务重置、模型额度耗尽后的审批恢复、业务消息 ID 优先级、429 / 503 恰好三次尝试与 401 一次失败、正文 / 推理 / 工具参数片段后中断不重试，以及应用组装入口的用量观测启停。普通问答在观测开启和关闭时都只有一次模型请求。

核实版本为 LangChain 1.3.14。官方 `after_model` 钩子倒序执行，HITL 暂停时待审批模型步骤尚未进入后续计数器，因此暂停快照可能比物理请求数少一个；恢复后补计，额度已耗尽时拒绝下一模型请求。官方 HITL 拒绝分支保留原 ToolCall 并添加 error ToolMessage，工具预算仍计入该请求，实际副作用为零。审批恢复测试同时核对预算与实际写入次数，避免把“拒绝执行”误判为“撤销调用预算”。

2026-10-04 由 `gpt-6-luna / xhigh` subagent 独立执行新 13 个案例和相关 34 个回归，各连续两次全部通过；主 Agent 的 `scripts/check.sh` 全量通过。案例矩阵、命令和审阅结论见 [行为验收记录](../exec-plans/completed/middleware-behavior-validation.md)。

参考：[官方 middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)、[Deep Agents 上下文工程](https://docs.langchain.com/oss/python/deepagents/context-engineering)。已安装版本的实际 API 与默认组装作为实现依据。
