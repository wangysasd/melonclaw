# 定时任务简化验收

日期：2026-10-15。仅覆盖[当前设计](task-center.md)；已删除旧开发结构迁移及其专属测试。

## 验证范围

| 场景 | 可重复证据 |
|---|---|
| 主/子受限工具、混合批次拒绝、停止与共享预算 | [真实离线 Agent 图](../../tests/test_task_agent.py) |
| 正文脱敏、不公开内部内容、溢出/超时/取消/意外 interrupt | [执行测试](../../tests/test_task_execution.py) |
| 北京时间四种计划、正常轮询延迟、错过不补跑、重叠跳过 | [纯日历测试](../../tests/test_task_schedule.py)、[数据库调度](../../tests/test_task_scheduling_postgres.py) |
| 首次创建当前三表、重复更新、保存/运行幂等、双领取竞争、配额、原子领取、运行与聊天消息原子交付 | [数据库队列](../../tests/test_task_postgres.py) |
| 真实模型配置读取复用单个事务连接 | 数据库队列 `test_real_model_resolution_reuses_single_transaction_connection` |
| 按新任务定义重新运行、模型版本改变不阻止执行 | 数据库调度 `test_cancel_ack_completion_race_and_current_configuration_rerun` |
| 用户/租户隔离、自动暂停后编辑仍暂停 | [进程和账户验收](../../tests/test_task_acceptance.py) |
| APScheduler 实际触发、单实例竞争、连接失效禁止旧写入、Web 正常停止与强制退出后的中断收敛 | [进程内调度验收](../../tests/test_task_scheduler.py) |
| 同一任务固定会话、消息与运行事务回滚、并发 seq、普通聊天隔离、累计追问历史、租户旁路校验 | [会话交付回归](../../tests/test_task_delivery.py) |
| 连续三次创作、相邻角色合并、本轮工具链保留、失败不推进、历史范围固定、原文读取、摘要复用、有限目标暂停与版本保护 | [任务历史回归](../../tests/test_task_history.py) |
| 对话创建绑定当前会话、真实消息来源、部分修改、启停、模型可用性、版本/幂等、租户/项目边界、单连接解析、主/子实际工具调用 | [对话配置回归](../../tests/test_task_configuration.py) |
| 新建省略模型仅选管理员系统默认、忽略聊天/个人默认、缺失/停用/无凭据明确报错、显式选择、编辑保留、默认切换后幂等重放 | 对话配置 `test_system_default_ignores_chat_and_personal_default_and_replay_survives_switch`、`test_unavailable_system_default_is_explicit_error_without_model_fallback`、[表单默认选择](../../frontend/tests/taskEditor.test.tsx) |
| 后台消息增量同步、保留普通聊天流、可见性取消、固定会话导航 | [前端交付回归](../../frontend/tests/taskDelivery.test.tsx)、[任务列表](../../frontend/tests/taskCenter.test.tsx) |
| 聊天创建后的绑定同步、开启后台交付、保持历史与活流 | [聊天生命周期回归](../../frontend/tests/chat-stream.test.tsx)、[交付状态](../../frontend/tests/taskDelivery.test.tsx) |
| 定时任务不展示报告正文，打开选定任务的固定会话；重新运行使用当前版本 | [页面回归](../../frontend/tests/taskCenter.test.tsx) |
| 列表名称/模型/计划/状态/最近/下一次独立列、北京时间与每周星期、无后续计划原因、五个操作及归档禁用 | [列表回归](../../frontend/tests/taskCenter.test.tsx) |
| 归档确认默认聚焦取消，取消不发请求，明确确认后按选中任务当前版本与幂等键归档 | [归档确认回归](../../frontend/tests/taskCenter.test.tsx) |
| 名称直达固定会话、最近运行打开抽屉、关闭保留筛选/页码；历史选择/分页/刷新、旧响应取消、跨任务拒绝、失败快照及取消终态等待 | [列表入口](../../frontend/tests/taskCenter.test.tsx)、[运行记录抽屉](../../frontend/tests/taskRunsDrawer.test.tsx) |
| 编辑暂停任务保持关闭；表单无项目/Skill/时区；网络重试复用键 | [编辑器回归](../../frontend/tests/taskEditor.test.tsx) |
| 北京时间转换、轮询生命周期 | [时间转换](../../frontend/tests/taskSchedule.test.ts)、[轮询](../../frontend/tests/taskPolling.test.tsx) |
| 全局导航无任务通知入口，保留定时任务与聊天草稿 | [导航回归](../../frontend/tests/workspace-navigation.test.tsx) |
| 普通 AI 布局、正文不变、无任务配置/引用入口、后台生成/失败/取消真实反馈 | [聊天回复展示](../../frontend/tests/chatview-draft.test.tsx) |

| 摘要总时限/取消/调用预算、UTF-8 无损切块、完整运行准备时限、只读锁/PID/断线、变化落库、模型名、标题、回执过期、保留数据升级 | [可靠性回归](../../tests/test_task_reliability.py)、[准备阶段释放锁](../../tests/test_execution_prepare.py) |
| 保存动作加载反馈、不同任务并发与乱序完成隔离 | [编辑器](../../frontend/tests/taskEditor.test.tsx)、[任务列表](../../frontend/tests/taskCenter.test.tsx) |

## 环境和命令

完整静态/离线回归：`scripts/check.sh`。数据库测试仅在受控环境设置 MELONCLAW_TEST_DATABASE_URL 后执行，创建并删除随机 test_tasks_* schema，不修改业务 schema。进程测试使用真实 FastAPI/Cookie/Web 内置 APScheduler，模型为离线替身。前端生产构建执行 `npm --prefix frontend run build`。

本轮不调用真实付费模型/公开搜索，不部署线上。Linux systemd 的安装和日志轮转仍需服务器实机验证；本地 PostgreSQL 测试不能替代这部分验收。完整 scripts/check.sh（启用真实 PostgreSQL 隔离测试）、前端构建和本地接口冒烟均已通过；再次 db-update 无变化。本地数据库结构与当前 schema 一致，任务、报告和聊天保留；仅清理五条含旧协议的 task_operations 回执。

2026-10-15 累计历史执行与追问回归采用真实离线 Agent 图和隔离 PostgreSQL，验证第三次请求读取前两次正文，数据库仍保留分开的助手消息。本次未改表，无新增数据库升级步骤。最终9项历史专项、完整 scripts/check.sh（含隔离 PostgreSQL）、前端生产构建与本地 API/浏览器验证均通过；没有付费外部模型调用。

2026-10-15 对话配置新增 9 项专项测试，验证实际普通聊天 Agent 的 inspect/save 调用和子 Agent 越权尝试、当前会话绑定、真实消息来源、幂等重放、部分字段更新、版本冲突、历史与快照保留、模型不可用时暂停/恢复、单连接数据库解析及身份/项目边界；背景策略回归新增 configure_task 拒绝案例。完整 scripts/check.sh（含隔离 PostgreSQL）与前端生产构建通过，前端共 349 项测试。首轮完整检查的一项账户表单测试在同时构建时超时，单独重跑及随后无并行构建的完整检查通过；未修改该账户测试或提高超时。未新增表、升级步骤、兼容分支或清理业务记录。

任务默认模型随后按用户要求改为管理员设置的全局系统默认。新增 4 项数据库用例（对话配置专项共 13 项），配置与普通聊天模型回归共 30 项、编辑器 4 项及完整 scripts/check.sh（含隔离 PostgreSQL）通过；验证默认切换不会改写既有任务或破坏已提交请求的重放，缺失/不可用不退回其他模型。数据库 schema 保持，无需升级或清理历史。

同日任务回复改用普通 AI 布局，删除显式报告引用链路；任务列表分开展示名称、模型、计划、状态、最近/下一次运行，保留五个操作。后台生成/失败/取消、累计历史重试和普通聊天流同步回归保留；新增两个列表用例覆盖七列表头、北京时间与星期、开始时间、无后续计划原因和归档按钮禁用。最终完整 scripts/check.sh（含隔离 PostgreSQL）、前端 354 项测试及生产构建通过；本地重启后 Web/执行器就绪，引用旧接口 404，任务会话 ID/角色/正文摘要未变。无需数据库更新、清理历史或外部付费调用。

独立任务通知入口随后移除，删除弹出列表、前端通知轮询和专属测试/图标/事件导航；导航回归验证无通知按钮且定时任务保留。完整 scripts/check.sh（含隔离 PostgreSQL）和生产构建通过，浏览器确认实际按钮已消失。任务会话/侧栏同步与定时任务五个操作不变；本次不更改后端通知 API、已读字段或业务历史，无需数据库升级。

独立详情页随后删除，任务名称打开固定会话，最近运行打开 TaskRunsDrawer。列表/抽屉共 9 项专项验证历史选择/分页/刷新、快照/错误、取消等待终态、跨任务拒绝、迟到响应及关闭保留筛选/页码；完整 scripts/check.sh（含隔离 PostgreSQL）和生产构建通过。浏览器在临时页核对成功记录、名称跳转和关闭后的筛选状态，未操作用户当前确认框或真实触发/取消任务。数据、API、调度和权限均不变，无需数据库升级。


2026-10-15 可靠性收尾：新增 13 项可靠性与 2 项聊天准备回归，包含摘要总时限/取消/预算、UTF-8 无损切块、准备和执行阶段超时分类、只读锁/PID/连接失效、正文不变仅写一次且仍响应取消、自动模型名同步、默认/自定义标题、过期拒重、增量升级保留数据并可重复运行。新增 3 项前端回归验证两种保存动作及不同任务乱序完成的加载隔离。完整 scripts/check.sh（真实 PostgreSQL 随机隔离 schema）与前端生产构建通过，未调用付费 API。实际业务库未升级、Web 未重启；启用新版前须停服运行 db-update。见[实施与验证记录](../exec-plans/completed/2026-10-15-task-reliability.md)。
