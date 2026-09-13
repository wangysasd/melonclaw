# 技术债清单

只记录**已知且尚未解决**的问题。解决后移到「已偿还」，保留一行用于追溯。

最后核实日期：2026-09-13

## 待处理

| 编号 | 问题 | 影响 | 建议动作 | 依据 |
|---|---|---|---|---|
| D2 | 执行链路无测试 | `services/execution.py` 717 行、含幂等/锁/事务，回归风险高 | 补 `request_id` 幂等、会话锁冲突、消息对事务的用例 | `docs/QUALITY_SCORE.md` |
| D3 | 尚有 8 处 lint 告警属于未启用的规则（7×TRY004 异常类型写法、1×BLE001 盲捕获） | 这两类规则能拦住常见错误，但不能机械修复 | 逐个确认：`BLE001` 那处是 MCP 连接失败的有意兜底，倾向加 `noqa` 注明原因；`TRY004` 需按上下文调整异常类型 | `uv run ruff check --select TRY,BLE src tests` |
| D5 | 构建产物 `__pycache__`、依赖目录与源码混在树里 | 检索噪声大，容易误读文件规模 | 依赖 `.gitignore` 已覆盖，检索时注意排除 | 仓库结构 |
| D6 | 大文件倾向 | `memory/service.py` 726 行、`services/execution.py` 717 行、`output/events.py` 575 行 | 下一次改动这些文件时顺手拆分职责（上限 900 行由测试强制） | `tests/test_architecture.py` |
| D7 | 无本地可观测性栈 | agent 无法自行验证启动耗时、事件延迟等指标 | 需要时再加最小实现，不要提前建设 | `docs/QUALITY_SCORE.md` |
| D8 | 设计文档 `note/上传附件技术设计.md` 未版本化 | 协作者与 CI 不可见，无法校验新鲜度 | 定稿后搬入 `docs/design-docs/` 并更新索引 | `docs/design-docs/index.md` |
| D9 | CI 未在真实 PR 上验证过 | 流水线可能因环境差异失败，分支保护也未配置 | 下一次提交时观察两个 job，通过后再考虑设为必需检查 | `.github/workflows/check.yml` |

## 已偿还

| 日期 | 问题 | 处理方式 |
|---|---|---|
| 2026-09-13 | `AGENTS.md` 只有规则、没有知识地图，且“提交前运行检查”没有可执行命令 | 新增知识地图与依赖方向章节，检查命令落到 `scripts/check.sh` |
| 2026-09-13 | README 混入 UI 实现细节 | 迁移到 `docs/FRONTEND.md` |
| 2026-09-13 | D1 没有 CI | 新增 `.github/workflows/check.yml`，与 `scripts/check.sh` 覆盖相同范围 |
| 2026-09-13 | 后端 lint 无配置，结果随 ruff 版本漂移 | `pyproject.toml` 增加显式 `[tool.ruff]`，钉住规则集与目标版本 |
| 2026-09-13 | D4 `pytest` 未声明为项目依赖 | 在 `[dependency-groups] dev` 中声明 `pytest`、`ruff`；`check.sh` 与 CI 统一改用 `uv run` |
| 2026-09-13 | D3 的导入顺序部分（23×I001） | `uv run ruff check --fix --select I` 整理 23 个文件，并把 `I` 纳入强制规则集 |
