# 用户决策 HITL

状态：已实现

## 背景与目标

工具审批解决“这个副作用操作能不能执行”，但不能解决“任务有多个合理路径时用户想要哪一条”。本能力让主 Agent 在缺少关键偏好、存在多个无法自动取舍的方案，或 Skill 明确要求选择时，主动向用户提问；用户回答后，Agent 从原来的 LangGraph Checkpoint 继续执行。它对应隔壁 Yuxi 的 `ask_user_question` 能力；MelonClaw 内部采用更短的 `ask_user` 工具名。

## 方案概览

- `core/user_input.py` 注册主 Agent 专用的 `ask_user` 工具。工具只接受 `questions` 数组，一次 `interrupt()` 交出一张卡片；只问一个问题时就是长度为一的数组，协议里不再有单题专用的字段布局。
- `core/hitl.py` 读取 Checkpoint 的真实 interrupt ID，区分工具审批和用户问题，并只把安全字段序列化给浏览器。
- `user_interactions` 记录问题 payload、Checkpoint interrupt ID、有效期、答案摘要和恢复状态；表只承载用户问题，因此不重复保存固定为 `user_question` 的类型列。`services/user_input_execution.py` 重新校验归属、当前问题和答案后构造 `Command(resume={interrupt_id: answer})`。
- 账本的六个状态（waiting / accepted / resolved / expired / recovery_required / discarded）与允许的转换只在一处定义：`repository/user_interaction_lifecycle.py`。repository 写库时的 where 与 CAS 条件由转换表反向生成（`transition_sources`），services 只用常量与语义分组（`OPEN_STATUSES` 等），不再手写字面量；schema 的 CHECK 约束与常量的对齐由测试强制。
- `POST /api/conversations/{conversation_id}/user-input` 接受答案并复用现有 Conversation advisory lock、SSE 和执行状态。
- 前端接收 `user_input_required`，显示 `UserQuestionPanel`；刷新历史时从 `pending_interaction` 恢复问题卡。
- 取消（用户点击跳过，或 TTL 过期后由服务端代答）复用同一条恢复通道：答案是 `{"type": "cancelled"}`，仍然产生一次 `Command(resume=...)`，Agent 醒来后自行收尾，不新增路由。

## 关键设计选择

用户问题和工具审批共用 LangGraph Checkpoint 与会话锁，但使用不同的工具、事件和 API，避免把用户偏好误当成副作用授权。一张问题卡片可以包含多个独立问题，每个问题支持单选、多选或自定义文本；多选可在允许时附加自定义文本。用户必须一次回答卡片中的全部问题，服务端逐题重新校验，浏览器不能伪造选项标签。

工具审批提交另外携带 `approval_batch_id` 和 `assistant_message_id`。批次 ID 由助手消息 ID 与当前 Checkpoint interrupt ID 集合稳定生成，因此刷新页面不会改变同一批审批；服务端恢复前会同时校验两个绑定，旧卡片或跨消息提交直接返回 409，避免误恢复另一轮审批。

“用户不回答”同样是一种需要处理的输入。Checkpoint 只有收到 `resume` 才会解除挂起，所以跳过被建模成第四种答案 `{"type": "cancelled"}`，而不是把 `user_interactions` 标成 discarded 之类的删除动作：后者只改业务账本、不动 Checkpoint，发新消息仍然会被拒绝。取消可以由用户点击触发，也可以由 `services/user_input_execution.py` 在收到新消息之前代答触发；两条路径共用 `decision_request_id` 幂等与会话锁，只有服务端代答才允许接收已过期的账本（`allow_expired`，并写入 `reason_code = cancelled_after_expiry`）。

**发新消息前的代答取消只有一个入口**（`cancel_before_new_message`）。它只查一次账本候选，用 `CancelReason` 把状态翻译成原因，再按 `_CANCEL_POLICY` 里声明的策略收尾，不再按原因各自开一个方法：

| 原因 | 账本状态 | 定位助手消息 | 过期校验 | 取消前改写 |
|---|---|---|---|---|
| `EXPIRED` | `waiting`（已过期）/ `expired` | 未完成的那一轮 | 必须已过期 | 无，原地接收旧账本 |
| `RECOVERY_REQUIRED` | `recovery_required` | 最新一轮 | 不适用 | 新建账本 → 丢弃旧账本 → 助手回滚成 `interrupted` |

策略表之外没有第二条分支：原因映射集中在 `_cancel_reason()`，账本只查一次意味着「判断要不要取消」和「真正取消」读的是同一份数据。新增取消原因时只要往 `CancelReason` 和 `_CANCEL_POLICY` 各加一行。

含 `ask_user` 的工具批次必须只有这一个调用：`interrupt()` 只暂停发起它的那一个调用，同一批的 `write_file` / `execute` 会照常执行，副作用会先于用户回答发生，恢复执行时还可能被重放一次。`middleware/user_input_guard.py` 在工具执行层把这类批次**整批短路**——本批每个调用各自返回一条与原 `tool_call_id` 对应的错误结果，让模型下一轮单独提问。只拦 `ask_user` 自己等于没拦，同批的副作用工具照样跑完。

客户端能力协商：只有随消息声明 `user_input_v1` 的浏览器才会拿到 `ask_user`，未知能力一律丢弃。能力归一化后写入消息 `display_metadata`，并进入 Agent 缓存键 `(project_id, model, capabilities)`；回答、审批和过期代答三条恢复链都沿用原消息的那一份，避免“提问时有 `ask_user`、恢复后没有”的错位。旧客户端因此不会拿到它渲染不了的卡片，只会遇到用普通文字追问的助手。

答案接收使用 `decision_request_id` 和答案摘要实现幂等：同一请求重试返回确认回执，不同答案会被拒绝。接收顺序严格是「取会话锁 → 锁内重读助手消息与 Checkpoint pending → 校验 → 接收」：在锁外先读快照会让并发提交的两个标签页各自通过校验，失败方最后收到的是一句误导性的 409。有效期默认 24 小时，可通过 `MELONCLAW_USER_INPUT_TTL_SECONDS` 调整。

答案已经收下但本轮没能跑完时，账本被标成 `recovery_required`（§5.4）。这是**禁止自动重放**的状态：既不知道 Agent 干到了哪一步，也不知道还会不会产生副作用，因此不会猜一个结果、也不会替用户重放一遍。此时历史不再把助手复活成“等待回答”，而是表现为明确失败；唯一的解锁入口是用户发起新一轮消息——那是一个明确的放弃信号，服务层会先用取消答案把 Checkpoint 叫醒收尾，再执行新消息。

## 数据与事件流

```text
Agent 调用 ask_user(questions=[...])
  → 一次 interrupt(问题批次)
  → Checkpoint 保存暂停状态
  → stream_execution 创建 user_interactions 记录
  → SSE user_input_required
  → 浏览器集中提交全部 answers
  → 服务端逐题校验并记录 accepted
  → Command(resume={真实 interrupt_id: 批量规范化答案})
  → 原 Agent 继续
  → SSE completed / 下一次 user_input_required / approval_required
```

## 失败与安全边界

- 当前用户的唯一有效租户归属、Project、Conversation 和助手消息归属都由服务层重新校验；请求体中的 ID 不是授权依据。
- 问题和答案只接受 JSON 可表达的有限字段；问题、选项、描述和自定义答案有长度限制，选项 ID 必须来自当前 Checkpoint。
- `ask_user` 不加入 Interpreter PTC，也不替代文件、Shell、删除等副作用工具的审批。
- 过期答案返回 410；答案冲突返回 409（并发提交、抢不到会话锁时是 409 会话忙）；答案本身不合法（选项不存在、不允许自定义）返回 422 且带 `error_code = user_answer_invalid`，和“这一轮已经变了”区分开。服务端不会接受客户端附带的模型问题文本或选项标签作为可信来源。
- 过期问题不会永久阻塞会话：收到新消息前，服务端会以取消答案代答一次来解锁 Checkpoint；代答失败只记日志，原错误照旧返回，不会比不代答更糟。
- 幂等重试命中同键已接受账本时返回回执流（`done` 事件 `terminal_reason = already_accepted`），浏览器据此重新拉取一次历史对账，而不是把卡片清掉就什么都不做。
- 数据库层保证同一 Conversation 至多一个等待中的问题（`user_interactions` 上的部分唯一索引）；服务层也强制一次只有一道题。
- 当前尚未支持同一轮多个用户问题、子 Agent 问题冒泡、独立的恢复任务和生产级身份认证。

## 运行与验证

修改数据库结构后执行：

```bash
uv run melonclaw-db-init
```

验证记录：`tests/test_user_input.py`、`tests/test_architecture.py`、`frontend/tests/user-question.test.tsx`、`frontend/tests/chat-stream.test.tsx`、`frontend/tests/approval.test.tsx` 和 `frontend/tests/stream-capabilities.test.ts` 已覆盖规范化、答案防伪、取消与过期解锁、interrupt/resume 负载、批次护栏、能力协商与缓存键、待恢复账本封账、锁外不写库、422 错误码、回执流对账和组件交互；真实 PostgreSQL Checkpointer、模型 API、数据库初始化（`uv run melonclaw-db-init`）和生产部署仍需在目标环境做 smoke test。
