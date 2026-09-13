# 质量评分与差距

按领域记录当前状态和已知差距。**数据以实测为准，改动后请更新对应行的数值和日期**，不要凭印象写“基本完善”。

最后核实日期：2026-09-13

## 评分

| 领域 | 评分 | 现状（实测） | 主要差距 |
|---|---|---|---|
| 架构约束 | 中 | 包职责边界清晰；`tests/test_architecture.py` 强制 8 组禁止依赖、入口独占、规则表完整性、源文件长度上限 | 只覆盖包级 import，未覆盖同一包内的模块粒度，也未约束横切入口的单一性 |
| 后端测试 | 低 | `tests/` 5 个文件、22 个用例全部通过；其中 7 个是结构/文档校验，业务用例仍只有 15 个。`src/melonclaw/` 共 72 个 py 文件、约 8300 行 | 执行链路（`services/execution.py` 717 行）、仓储事务、迁移、SSE 事件序列均无测试 |
| 前端测试 | 中 | `frontend/tests/` 10 个用例文件，覆盖聊天流、审批、Markdown、技能选择；`npm test` 通过 | 无 E2E / 集成测试，SSE 契约靠单元测试手工维护 |
| 静态检查 | 中 | 后端 ruff 已显式配置（`pyproject.toml` 的 `[tool.ruff]`，规则集钉死为 `E4/E7/E9/F/I`），`uv run ruff check src tests` 通过；前端 eslint + tsc 通过 | TRY（7 处）与 BLE（1 处）规则尚未启用（见 D3） |
| 依赖环境 | 良 | `pyproject.toml` + `uv.lock` 固定版本，`uv lock --check` 通过；`pytest`、`ruff` 已在 `[dependency-groups] dev` 中声明 | `npm ci` 依赖前端锁文件；无其它缺口 |
| CI | 中 | `.github/workflows/check.yml` 分后端 / 前端两个 job，覆盖编译、测试、lint、类型检查、锁文件 | 尚未在真实 PR 上验证过；无缓存之外的优化，无分支保护配置 |
| 文档 | 中 | `docs/` 为事实来源，`AGENTS.md` 提供地图；相对链接与地图条目由 `tests/test_docs_links.py` 校验 | `note/` 未版本化（本地草稿）；无文档新鲜度倒计时机制 |
| 可观测性 | 中 | 后端有结构化运行提示与脱敏日志；前端有可展开的执行摘要 | 无本地 metrics/trace 栈，agent 无法自行查询运行指标 |
| 安全边界 | 中 | HITL 审批、凭据硬黑名单、归属校验都在服务层 | `LocalShellBackend` 无隔离；无病毒扫描；多租户认证为开发模拟 |

## 已修复

| 日期 | 项目 |
|---|---|
| 2026-09-13 | 建立 `docs/` 知识库与相对链接校验测试 |
| 2026-09-13 | README 中的 UI 实现细节迁移到 `docs/FRONTEND.md` |
| 2026-09-13 | 新增 `scripts/check.sh` 与 GitHub Actions 流水线，检查从人工变成可执行 |
| 2026-09-13 | 后端 ruff 显式配置，消除“lint 结果随 ruff 版本变化”的不确定性 |
| 2026-09-13 | 整理 23 个文件的导入顺序（`I` 规则），并把 `I` 纳入强制规则集 |
| 2026-09-13 | `pytest`、`ruff` 声明为 dev 依赖，消除“跑错解释器”的假通过风险 |

## 环境陷阱（会误导判断）

1. **不要裸跑 `pytest` / `ruff`**。两者已声明为 dev 依赖，统一用 `uv run pytest` / `uv run ruff`；直接敲 `pytest` 可能命中 PATH 上其它项目的同名工具，产生假通过。
2. **服务启动不迁移数据库**。`verify_schema` 只做校验，建表和迁移必须显式执行 `uv run melonclaw-db-init`。
3. **lint 规则集写在 `pyproject.toml`**。不要在没有配置的情况下直接跑 `uvx ruff check`，不同 ruff 版本的默认集不同（0.16 的默认集包含 I / TRY / BLE）。

## 改进优先级

1. 在真实 PR 上跑通 CI，确认两个 job 都能稳定通过。
2. 补执行链路测试：至少覆盖 `request_id` 幂等、会话锁冲突、消息对事务、SSE 事件顺序。
3. 整理剩余 8 处 lint 告警（`uv run ruff check --select TRY,BLE src tests` 可复现），然后扩大 `select`。
4. 提升可观测性，让 agent 能自行验证“启动耗时”“事件延迟”这类可测目标。
