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
  `message_id`。子 Agent 继续沿用任务卡事件。
- `chat_messages.assistant_steps` 只在中断、完成、失败、取消等生命周期边界整体写入，
  `content` 只保存最终答复，`execution_duration_ms` 保存活跃执行耗时。
- 前端 reducer 按 `message_id → step_id → call_id` 更新；流式、pending 和 interrupted
  阶段按 ordinal 直接展示完整的 AIMessage / tool call 交错序列，不提前猜测哪条是最终
  答复。进入终态后，后端标记 `is_final` 的那条 AIMessage 才分离为正文；运行期间过程
  展开，进入终态后默认收起但可手动展开。`completed` 同时返回已提交的
  `assistant_steps` 完整快照，用于对账漏收的过程增量；这是新协议的必填字段，前端不再
  为缺失快照或缺失 `is_final` 保留旧数据推断分支。AI 文本与工具卡使用独立 DOM 和
  视觉容器，不再渲染 `ReasoningSummary`。历史直接读取同一 JSON 结构。
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


## 2026-10-03：完整 content 展示

用户要求显示模型推理期已经返回的 content，避免长时间只有状态。删除后端 think 过滤和前端 Markdown／历史／流式 JSON 猜测过滤；原始 v3 文本及 reasoning 增量走同一文本事件，结构化 content 的 text、reasoning、thinking 块按顺序提取。工具选择器的来源隔离见下文。模型没有返回的内部状态不能被展示。此前已过滤落库的历史保持现有内容，不从 Checkpoint 回填。

运行使用原有 scripts/restart.sh，无依赖或数据库结构变化。tests/test_model_content.py、tests/test_model_activity.py 验证跨片标签即时转发、未结束推理、子 Agent、结构化 reasoning、最终快照；前端验证 Markdown、流式与历史保留同一文本。完整验证入口为 scripts/check.sh。

## 内部工具选择器隔离（2026-10-03）

完整正文展示暴露了原先依赖前端猜测 JSON 形状遮掩的问题：内部
`{"tools": [...]}` 被投影为根 Agent 的 AIMessage。核实 `uv.lock` 的
LangGraph 1.2.10：`MessagesTransformer` 读取调用元数据用于路由，但不会将
`tool_selector` 和 tags 挂到 `ChatModelStream`；`_start_metadata` 只是模型协议的
启动信息。升级框架时需要重新核实这些行为。

选择器模型调用增加框架公开 `TAG_NOSTREAM`（见
[官方流式说明](https://docs.langchain.com/oss/python/langgraph/streaming#disable-streaming-for-specific-chat-models)），
使其流式片段和最终模型消息均不进入 messages 投影。选择器输出仍由中间件解析，
仅用于筛选主模型工具，不写业务历史。主模型调用继续原样展示 JSON 与推理。

事件流：中间件 `runtime.stream_writer` 发出 `selecting_tools` → 内部选择调用 →
解析与筛选 → 发出 `waiting_model` → 主模型正文/实际工具调用。`output/events.py`
注册公开 `CustomTransformer` 并发消费根命名空间的 custom 通道，仅转发这两个
固定阶段字段；未知类型、字段及子命名空间阶段不会进入根 Agent 状态。
没有新增工具、外部写入权限、数据库结构或历史兼容分支。

该阶段最初采用选择失败和取消原样传播，后续选择失败改为有界降级（见下文），取消仍传播；已落库的选择器 JSON
不自动清洗。运行沿用 `scripts/restart.sh`，无需重建数据库。预期是选择期间顶部
显示「正在选择工具」，完成后显示「等待模型响应」，正文和执行步骤中不出现内部
候选 JSON。`tests/test_tool_selector_stream.py` 通过真实框架 v3 消息流离线验证
流式/非流式隔离、完成快照、选择阶段在响应完成前可见、实际工具筛选与执行、
正常 JSON/推理完整保留，以及失败、取消与 custom 字段限制。

验证记录：专项及关联回归 20 项通过，`scripts/check.sh` 全量通过。模型使用离线
fake，实际消费本地安装框架的 v3 投影；没有调用真实供应商或改写数据库。
本地开发服务已通过 `scripts/restart.sh` 重启，后端 ready、前端 HTTP 200。

## 改进前的回复流畅性核查（2026-10-03）

`tests/test_tool_call_content_stream.py` 通过真实 `ProviderChatOpenAI`、Agent 图与 v3
投影消费离线 HTTP SSE，验证同一条带 `tool_calls` 的 AIMessage 中，调用前文本、
与参数同片返回的文本均在模型完成/工具结果返回前转发；完成快照保留相同正文。
前端 `agent-execution.test.tsx` 验证工具尚在运行时该步骤文本仍可见且可更新。
这验证标准文本链路，不构成所有真实供应商输出字段和延迟的保证。

当前 `tool_selector` 与主 Agent 使用同一个模型。候选目录超过 16 项时，每次
主模型请求前都会等待一次完整的选择请求，只提供最近 HumanMessage 和目录，
没有按用户轮次缓存；工具返回后及生成最终回答前都会再次触发。两轮主模型的离线
HTTP 实测顺序为 `selector → answer → selector → answer`。`TAG_NOSTREAM`
隔离的是内部选择器的消息展示，并未移除该模型请求或它的耗时。

已知边界：当前统一 `ChatOpenAI` 适配器不会提取第三方供应商的
`reasoning_content` / `reasoning_details`，见
[官方适配器说明](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI)。
这些字段在变成标准 AIMessage 前已丢失；本地转换探针已核实 reasoning_content
在流式和整条消息转换后都未保留。普通 content 内的 `<think>` 与标准 reasoning
块继续展示。文本提取只覆盖 text/reasoning/thinking 块，仍受脱敏与长度边界约束，
不承诺无限制保留整个供应商原始响应。

只有工具参数增量时不生成自然语言正文；实际工具执行期间主模型通常等待结果。
当前提示词没有要求长工具工作前主动说明行动，因此正文转发完整也可能长期停字。
后续改善应分别处理选择器重复调用、供应商推理适配、简短行动说明和阶段计时。
本次仅增加验证，不改变选择策略、提示词、数据库、依赖或工具审批。
验证记录：HTTP SSE 专项 2 项通过，前端对应文件 33 项通过；`scripts/check.sh`
全量通过。未调用真实供应商，当前没有各阶段实际延迟的量化结论。

## 核查后的改进（2026-10-03）

选择器现已按逻辑用户轮次复用，用 `find_tools` 扩展遗漏工具，超时/无效响应只降级一次。每项工具流并发消费，快工具结果无需等待慢工具；调用声明为 queued，实际执行才计时。第三方推理协议由供应商配置明确选择，原始字段完整保留在 Checkpoint 并回传，UI 的有序正文/推理块单独脱敏限长。正文增量带 `content_kind`，步骤有 `content_blocks`，阶段记录在 `display_metadata.timings`。完整设计与验证入口见 [回复流畅性与推理协议](response-fluency.md)；上节反映改进前行为，不再代表当前选择策略或适配能力。
