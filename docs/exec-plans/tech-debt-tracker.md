# 技术债清单

只记录**已知且尚未解决**的问题。解决后移到「已偿还」，保留一行用于追溯。

最后核实日期：2026-10-17（限仓库代码与本机审计；CI、生产状态保留待核实；已核对并整理本地草稿）。

## 待处理

| 编号 | 问题 | 影响 | 建议动作 | 依据 |
|---|---|---|---|---|
| D2 | 完整 Agent/Checkpoint/SSE 组合仍缺可重复集成验证 | 已有账户、任务与产物的 PostgreSQL 随机隔离 schema 测试；2026-10-17 审计未提供专用连接，59 项数据库测试跳过，不能据此宣称完整执行链路已验证 | 持续运行现有隔离测试，再补完整 Agent 事件序列、Checkpoint 恢复与 SSE 投递的组合场景 | `docs/QUALITY_SCORE.md`、`tests/test_accounts_postgres.py`、`tests/test_task_postgres.py`、`tests/test_artifact_postgres.py` |
| D3 | 未启用的 TRY/BLE 扩展规则仍有告警 | 2026-10-17 审计基线为 435 条：TRY003×320、TRY301×54、BLE001×28、TRY004×19、TRY300×13、TRY203×1；常规 lint 通过，这些告警不等于独立缺陷 | 按用例审查异常类型、取消传播与有意兜底，再决定规则启用范围，不能机械改写或批量加 `noqa` | `uv run ruff check --select TRY,BLE src tests --statistics`；[审计基线](completed/2026-10-17-code-cleanup-audit.md#验证基线) |
| D5 | 构建产物 `__pycache__`、依赖目录与源码混在树里 | 检索噪声大，容易误读文件规模 | 依赖 `.gitignore` 已覆盖，检索时注意排除 | 仓库结构 |
| D6 | 大文件倾向 | 2026-10-17 复核：`memory/service.py` 730 行、`services/execution.py` 862 行、`repository/attachments.py` 857 行，均未超过 900 行 | 继续按事务和用例职责评估拆分，不为凑行数增加转发层（上限 900 行由测试强制） | `tests/test_architecture.py` |
| D7 | 无本地可观测性栈 | agent 无法自行验证启动耗时、事件延迟等指标 | 需要时再加最小实现，不要提前建设 | `docs/QUALITY_SCORE.md` |
| D9 | 真实 PR 的 CI 运行与分支保护状态待核实 | 仓库已有双 job 流水线，本机检查无法证明线上环境与必需检查配置 | 查看实际 PR 运行历史与保护配置，确认两个 job 稳定后再决定是否设为必需检查 | `.github/workflows/check.yml` |
| D10 | Shell 沿用受信任本地开发免审批例外 | `execute` 未登记在当前审批清单；`LocalShellBackend` 不是沙箱，误操作/提示注入可直达宿主机 | 先设计隔离与授权边界，再评估 `core/hitl.py` 的 `TODO(sandbox)`；旧注释配置本体已删除，不能靠取消注释恢复 | `src/melonclaw/core/hitl.py`、`docs/ARCHITECTURE.md` 第 5.1 节 |
| D11 | 长会话流持续占用业务数据库连接 | 同时执行数接近连接池上限时，普通业务查询会等待 | 记录连接池等待和锁持有时长；确认负载后优先评估独立限额锁池，再设计可保护 Checkpoint 的租约方案 | `repository/locks.py`、`database/database.py` |
| D13 | 后台 SSE 包装器两项边界缺口 | producer 异常未取回、首次迭代前关闭源的清理仍为 strict xfail；低层测试不能单独证明 ASGI 真实请求泄漏 | 保留两项测试并补独立生命周期/断线端到端证据，不靠清理测试规避 | `tests/test_review_background_stream.py` |
| D12 | 前端主入口仍较大 | 2026-10-11 构建仍有超过 500 KB 的入口/图表分块，最大 index 块约 1.56 MB（gzip 524 KB） | 用浏览器网络记录和构建分析确定首屏依赖，再懒加载非首屏弹窗；仅配置 `manualChunks` 不减少总下载量 | `npm run build` |

## 审计结束后的独立评估记录

2026-10-17 用户结束本轮代码清理。以下为[已归档审计](completed/2026-10-17-code-cleanup-audit.md)中的未实施候选；它们不全是已确认缺陷，也不构成继续执行授权。以后确有需求时重新核对代码与证据，避免重复展开已结束的审计。

| 候选 ID | 当前记录 | 后续边界 |
|---|---|---|
| CLN-014 | 两种刷新 Hook 有重复循环，但业务语义不同 | 默认保持分离，不扩大普通会话正文轮询 |
| CLN-016、018 | 大模块职责与前端首屏分块 | 需要独立拆分方案或性能测量；同 D6/D12 |
| CLN-028、031 | 仅测试使用的前端函数、重复 UTC helper | 先明确覆盖去向和时间求值位置 |
| CLN-019 | 直接依赖的外部 Skill 消费者未验证 | 不凭源码没有 import 删除依赖 |
| CLN-020、030 | 旧 action 与全局运行状态 | 先证明身份/流生命周期和渲染时序等价 |
| CLN-021、027、029 | 公开 Python 方法、导出与 MCP loader | 核实外部接口契约和取消/异常语义 |
| CLN-022 | 旧审批展示与恢复分支 | 历史 Checkpoint/快照消费者未排除，保留 |
| CLN-023、038 | 品牌图与图标资源 | 动态名称及外部静态 URL 未排除 |
| CLN-026、032、034 | 未读参数、账本判断与安装身份核验 | 涉及消息协议、事务或权限，独立验证 |
| CLN-033 | 密码命令的第二次环境加载 | 可能改变配置来源，不能直接视作等价重复 |
| CLN-037 | 未挂载的页脚声明组件 | 文档误述已修正，产品去留尚未决定；不删除当前访问提示 |

## 已偿还

2026-10-17 文档整理：D8 的附件草稿已合并至现行附件设计和架构能力边界；OCR/对象存储等未实现扩展及旧框架行号不保留为当前功能说明。

| 日期 | 问题 | 处理方式 |
|---|---|---|
| 2026-09-13 | `AGENTS.md` 只有规则、没有知识地图，且“提交前运行检查”没有可执行命令 | 新增知识地图与依赖方向章节，检查命令落到 `scripts/check.sh` |
| 2026-09-13 | README 混入 UI 实现细节 | 迁移到 `docs/FRONTEND.md` |
| 2026-09-13 | D1 没有 CI | 新增 `.github/workflows/check.yml`，与 `scripts/check.sh` 覆盖相同范围 |
| 2026-09-13 | 后端 lint 无配置，结果随 ruff 版本漂移 | `pyproject.toml` 增加显式 `[tool.ruff]`，钉住规则集与目标版本 |
| 2026-09-13 | D4 `pytest` 未声明为项目依赖 | 在 `[dependency-groups] dev` 中声明 `pytest`、`ruff`；`check.sh` 与 CI 统一改用 `uv run` |
| 2026-09-13 | D3 的导入顺序部分（23×I001） | `uv run ruff check --fix --select I` 整理 23 个文件，并把 `I` 纳入强制规则集 |
