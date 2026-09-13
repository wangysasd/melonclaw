# MelonClaw 知识库索引

这个目录是本仓库的事实来源（system of record）。根目录的 `AGENTS.md` 只提供地图，具体细节在下面这些文档里。

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块分层、依赖方向、横切关注点入口、关键取舍 | 改动模块边界、新增包或跨层调用之前 |
| [FRONTEND.md](FRONTEND.md) | 前端结构、开发与部署、UI 约定 | 改前端组件或样式之前 |
| [QUALITY_SCORE.md](QUALITY_SCORE.md) | 各领域质量评分与已知差距 | 规划改进、评估风险时 |
| [design-docs/index.md](design-docs/index.md) | 设计文档清单与状态 | 实现新功能之前 |
| [exec-plans/README.md](exec-plans/README.md) | 执行计划的写法与存放约定 | 开始复杂改动之前 |
| [exec-plans/tech-debt-tracker.md](exec-plans/tech-debt-tracker.md) | 已知技术债清单 | 做重构、补测试时 |
| [../README.md](../README.md) | 面向首次使用者的安装、配置、使用与安全边界 | 新用户上手、对外说明 |

## 本地草稿

`note/` 目录（相对仓库根目录）保存个人讨论记录和未定稿的设计，**不纳入版本控制**，也不作为事实来源。其中的结论在搬进 `docs/` 之前，只当作草稿看待。

## 维护约定

- 结论以本目录为准。`note/` 与 `docs/` 内容冲突时，以 `docs/` 为准并修正草稿。
- 代码改动导致下列内容变化时，同步更新对应文档：模块职责、依赖方向、环境变量、命令、用户可见行为。
- 所有相对链接由 `tests/test_docs_links.py` 校验。链接指向不存在的文件会让检查失败；指向被 `.gitignore` 忽略的本地文件（例如 `note/` 下的内容）会被自动跳过。
