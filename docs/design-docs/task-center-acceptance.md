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
| 任务会话在数据库分页前筛选、暂停/归档可见性、置顶跨页、刷新后加载更多去重与项目隔离 | [数据库交付回归](../../tests/test_task_delivery.py)、[会话状态回归](../../frontend/tests/session-new-conversation.test.tsx) |
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

当前回归映射见上表；最新本机检查与跳过项见[质量记录](../QUALITY_SCORE.md)。服务器 systemd、日志轮转、真实外部模型与线上负载需单独验证，本轮没有复测这些环境。

历史阶段中的独立报告页、报告引用、任务通知和 TaskDetail 均已退出当前实现，不再保留中间形态的验收步骤。当前任务结果在固定普通会话查看，运行记录在 TaskRunsDrawer 查看；通知列/索引的移除仍由已登记的 task_reliability 升级负责，升级不能删除。

现有测试的通过记录不能授权清理业务回执、消息或数据库；数据库测试仅使用专用连接和隔离 schema。当前文档整理没有运行 db-init/db-update、真实任务或业务请求。
