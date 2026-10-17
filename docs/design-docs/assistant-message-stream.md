# 完整 AI 消息流展示

状态：已实现

## 背景与目标

一次用户请求可能包含多轮根 Agent `AIMessage`。旧实现把文本追加到一条全局字符串，
工具生命周期另存为事件数组，因而无法表达“文本 A → 工具 → 文本 B → 最终答复”，
也无法在历史中恢复中间文本。本方案保持一条 user / assistant 业务消息对，在 assistant
消息内保存有序的 `assistant_steps`。

## 方案

- `output/assistant_steps.py` 是不依赖数据库的 projector / accumulator。
- 每个根 `AIMessage` 生成一个 `run_id + ordinal` step；工具按 `call_id` 归属，按
  `batch_index` 排列，逆序结果不会改变声明顺序。
- 根 Agent SSE 使用 `assistant_step_started`、`assistant_text_delta`、
  `assistant_tool_call`、`assistant_tool_result`、`assistant_step_completed`；事件都带
  `message_id`。子 Agent 使用独立 scope 事件并进入嵌套执行时间线。
- `chat_messages.assistant_steps` 只在中断、完成、失败、取消等生命周期边界整体写入，
  `content` 只保存最终答复，`execution_duration_ms` 保存活跃执行耗时。
- 前端 reducer 按 `message_id → step_id → call_id` 更新；流式、pending 和 interrupted
  阶段按 ordinal 直接展示完整的 AIMessage / tool call 交错序列，不提前猜测哪条是最终
  答复。进入终态后，后端标记 `is_final` 的那条 AIMessage 才分离为正文；运行期间过程
  展开，完成后有工具、可读思考或子 Agent 时默认展示时间线，纯过程文字可收起。`completed` 同时返回已提交的
  `assistant_steps` 完整快照，用于对账漏收的过程增量；这是新协议的必填字段，前端不再
  为缺失快照或缺失 `is_final` 保留旧数据推断分支。AI 文本与工具卡使用独立 DOM 和
  视觉容器。历史直接读取同一 JSON 结构。
- 工具快照额外记录服务端观测到的 `started_at` / `completed_at`（epoch 毫秒）及
  单调时钟 `duration_ms`；只有观测到实际执行才记录开始，不把 queued 或审批算入执行耗时。展示层的运行状态、计时、折叠与
  工具条目组织见 [Agent 执行过程展示](agent-execution-display.md)。

## 失败与安全边界

模型返回的文本 content（包括 `<think>` 中的推理、未结束推理、结构化 reasoning 内容块）逐片进入 projector，完成快照与历史保留相同内容；前端不再二次过滤。内部工具选择器通过公开 `TAG_NOSTREAM` 在调用源头隔离，不按正文 JSON 形状猜测，也不探测消息私有元数据。文本、工具参数和结果仍脱敏并受已有大小上限约束。中断/失败/取消会保留已有 steps，未返回结果的工具标记为 `waiting` 或
`unknown`，不会伪装成成功。新数据库按 `schema.py` 重建，不做旧消息回填或独立 parts 表。

## 验证

`tests/test_assistant_steps.py` 覆盖多轮顺序、逆序结果、提前到达工具事件和失败/HITL
保留内容；`frontend/tests/agent-execution.test.tsx` 覆盖 reducer 的 ID 归约、终态快照
对账和完成前后折叠状态。全量验证入口为 `scripts/check.sh`；真实模型多轮、PostgreSQL 初始化
和刷新回放仍需在配置可用的运行环境执行。

## 当前流与工具选择入口

output/events.py 使用框架 v3 消息、工具及 custom 流并发投影，output/assistant_steps.py 的 AssistantStepAccumulator 负责有序快照。工具声明、真实执行开始与结果分别更新状态；快工具结果不等待同批慢工具。子 Agent 通过 scope/namespace 归约到嵌套执行节点。

ToolPoolMiddleware 只在 find_tools 请求后委托官方选择器，不进行每消息或每次模型请求预选。工具池跨消息复用，目录变化失效；内部选择调用使用 TAG_NOSTREAM，阶段通过受限 custom 事件公开。详见[按需工具池](on-demand-tool-pool.md)。

供应商推理字段由显式 reasoning_format 和 ProviderChatOpenAI 适配；正文增量的 content_kind 与快照 content_blocks 保持思考/正文顺序，签名与加密数据不进入 UI。详见[回复协议](response-fluency.md)。普通聊天 SSE、当前任务会话增量同步与侧栏任务摘要各有职责，不扩大为所有会话正文轮询。

当前测试入口包括 test_tool_selector_stream.py、test_tool_call_content_stream.py、test_parallel_tool_stream.py、test_reasoning_protocol.py，以及前端 chat-stream 和 agent-execution。测试验证当前协议，历史供应商延迟样本不能代表当前性能。
