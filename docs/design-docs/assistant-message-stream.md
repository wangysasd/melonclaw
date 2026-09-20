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
- 工具快照额外记录服务端观测到的 `started_at` / `completed_at`（epoch 毫秒），
  前端据此显示工具耗时；没有可靠时间就不显示。展示层的运行状态、计时、折叠与
  工具条目组织见 [Agent 执行过程展示](agent-execution-display.md)。

## 失败与安全边界

文本、工具参数和结果在 projector 中过滤、脱敏和限长；不展示隐藏推理或工具选择器
内部 JSON。中断/失败/取消会保留已有 steps，未返回结果的工具标记为 `waiting` 或
`unknown`，不会伪装成成功。新数据库按 `schema.py` 重建，不做旧消息回填或独立 parts 表。

## 验证

`tests/test_assistant_steps.py` 覆盖多轮顺序、逆序结果、提前到达工具事件和失败/HITL
保留内容；`frontend/tests/agent-execution.test.tsx` 覆盖 reducer 的 ID 归约、终态快照
对账和完成前后折叠状态。全量验证入口为 `scripts/check.sh`；真实模型多轮、PostgreSQL 初始化
和刷新回放仍需在配置可用的运行环境执行。
