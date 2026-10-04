# 新增中间件行为验收

状态：已完成
开始日期：2026-10-04

## 目标与背景

用户要求设计测试案例，再让 Luna 6 extra-high subagent 执行并核对输出。现有运行控制已覆盖常规预算、摘要与工具池；本次补充审批决策、失败尝试次数、流中断和观测开关的行为验收。

## 步骤

- [x] 阅读当前实现与既有测试，明确输入、预期结果和可观察点。
- [x] 在 tests/test_middleware_behavior.py 添加 13 个参数化案例。
- [x] 使用 gpt-6-luna / xhigh subagent 独立执行新案例和相关回归，报告实际状态与差距。
- [x] 核对报告、处理发现的问题并运行 scripts/check.sh。
- [x] 将验证结果写入设计文档和本地 note，归档计划。

## 案例与验收标准

| 案例 | 输入 | 预期结果与可观察点 |
|---|---|---|
| 审批批准 / 拒绝（2） | Todo → 受控写操作 → 暂停 → Command 恢复 → 新任务 | 批准仅写一次，拒绝零写；暂停计数 1，恢复后补计至 3、Todo 保留；新任务计数回到 1、工具预算与 Todo 清零 |
| 额度耗尽后审批恢复（2） | 模型额度 1，产生待审批写操作，批准 / 拒绝，再开始新任务 | 首次恰好 1 次请求；恢复后下一模型调用被拒绝，模型调用仍为 1；新任务可再调用 1 次 |
| 业务任务边界（1） | 图消息 ID 改变，业务 user_message_id 先不变再改变 | 模型计数依次 1、2、1；任务 ID 跟随业务上下文 |
| 重试耗尽 / 鉴权（3） | 每次分别返回 429、503、401 | 429 / 503 恰好 3 次尝试，401 仅 1 次；最终错误码 model_execution_failed |
| 流式输出后中断（3） | 输出正文、推理或工具参数片段后网络失败 | 恰好 1 次模型尝试；部分输出错误；正文 / 推理不重复，不执行未完成工具 |
| 用量观测启停（2） | 应用真实组装入口，普通问答，开关 true / false | 都只调用模型 1 次；开启后输出上下文估算与 7 / 3 token 用量，关闭后两类事件均消失 |

相关回归继续覆盖摘要隐藏、隐藏大工具目录、主／子 Agent 工具池与 Todo 隔离、超额工具批次整体拒绝以及历史用量去重。

## 运行步骤

```bash
uv run pytest -q tests/test_middleware_behavior.py
uv run pytest -q tests/test_agent_controls.py tests/test_agent_tool_assembly.py tests/test_model_usage.py tests/test_official_tool_selector.py tests/test_tool_selection_lifecycle.py tests/test_tool_selector_stream.py
scripts/check.sh
```

## 边界与依赖

使用已安装的 Deep Agents / LangChain 真实图、内存 Checkpointer、临时工作区和离线模型 / HTTP mock。Luna 是测试执行与审阅者；应用内模型输入由确定性 fixture 提供。测试不读取凭据、不调用真实供应商、数据库或 MCP，不改变业务工具权限，不等同于真实供应商回复质量或费用验证。

## 审阅中发现

首轮 11 个案例中 9 个通过，2 个审批案例发现测试误判了计数提交时机。官方 after_model 钩子倒序执行，HITL 先中断，待审批模型步骤尚未进入计数器；恢复后补计并拦住超额的下一次请求。已据此修正暂停快照的预期，并补充额度耗尽后的批准 / 拒绝回归；保留恢复后完整计数和副作用断言。

已核对 LangChain 1.3.14 的 HumanInTheLoopMiddleware._process_decision：拒绝返回原工具请求与 error ToolMessage，保留请求供模型理解，ToolNode 因该请求已有结果而跳过实际执行。因此批准和拒绝都消费该请求的工具预算；两者副作用次数分别为 1 和 0。拒绝案例据此检查恢复后工具计数 2，而不是把拒绝当作撤销预算。

## 验证结果

Luna 独立执行最终 13 个案例与 34 个相关回归，各连续两次全部通过。完整额度 2 探针核对物理模型请求次数，恢复后第三次请求在出站前被拒绝；批准发生一次受控内存写入，拒绝零写入，Todo 保留，新任务重置正常。两处失败均为测试预期错误，未修改业务实现。

主 Agent 执行 scripts/check.sh 全部通过：后端编译、全量 pytest（含架构与文档链接）、ruff、锁文件和前端 lint、类型检查、测试。设计文档更新后再次运行文档链接测试与 git diff --check，通过。独立执行报告保留在 note/middleware-luna-review.md，长期行为说明写入 [Agent 运行控制与用量](../../design-docs/agent-runtime-controls.md)。
