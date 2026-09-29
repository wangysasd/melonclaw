# MelonClaw 开发约定

MelonClaw 是一个持续演进的 Deep Agents 应用。开发工作应围绕可运行能力、清晰边界和可验证结果展开。
注意不要写兼容性代码，现在是开发阶段，可以清空数据库历史数据。
系统的字号以14号为主。
本文件是**地图**，不是说明书。先在下面定位，再去对应文档读细节；不要指望本文件包含全部细节。

## 知识地图

| 想了解 | 去哪读 |
|---|---|
| 模块分层、依赖方向、横切入口、关键取舍与历史教训 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 前端结构、开发与部署、UI 约定 | [docs/FRONTEND.md](docs/FRONTEND.md) |
| 各领域质量评分、已知差距、环境陷阱 | [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md) |
| 设计文档清单与状态 | [docs/design-docs/index.md](docs/design-docs/index.md) |
| 执行计划的写法与存放约定 | [docs/exec-plans/README.md](docs/exec-plans/README.md) |
| 已知技术债 | [docs/exec-plans/tech-debt-tracker.md](docs/exec-plans/tech-debt-tracker.md) |
| 安装、配置、启动、功能使用、排障、安全边界 | [README.md](README.md) |

本地草稿目录 `note/` 不纳入版本控制，里面的内容只作参考，不作为事实依据。

## 工作方式

- 用户提供网页、文档或代码链接时，先阅读链接内容，再结合当前实现说明相关概念、设计取舍和关键代码。
- 用户要求新增能力时，应在现有应用上迭代、扩展或重构，保持 Web、数据库和 Agent 组装链路的一致性；不要为单个链接或单个功能创建独立项目。
- 新增功能完成后，更新 `note/note.md`，记录功能背景、问题定义、方案、关键取舍、实现位置、验证方式、可观察点和当前边界，语气让人容易理解。结论稳定后，把需要长期存在的部分搬进 `docs/`，并在 [docs/design-docs/index.md](docs/design-docs/index.md) 更新状态。
- README.md 面向首次使用者，持续维护安装、配置、启动、功能使用、排障和安全边界；不要把实现过程流水账堆进 README，实现细节放进 `docs/`。
- 模型只从数据库 `model_configs` 解析。初始化只写入无凭据、未启用的供应商模板，不创建默认模型、不读取模型环境变量。管理员配置的是全局内置模型，普通用户配置的是个人模型；没有可用模型时提示用户先配置，不从环境变量生成模型。供应商可显式配置 `api_key_env` 作为共享凭据兜底：全局模型按个人 Key → 数据库共享 Key → 指定环境变量解析，个人模型只使用个人 Key。
- 不得把 `.env` 中的 API Key、Token、数据库密码或完整凭据 URL 输出到终端、日志、文档、示例代码或回复中；示例只能读取环境变量。

## 代码与目录

- 应用代码统一维护在 `src/melonclaw/`，按配置、Agent 构建、提示词、工具、数据库、流式输出、Web 服务等职责拆分模块。
- 新增能力优先扩展既有模块或提炼可复用组件；只有职责足够独立时才在包内增加子包。目录和模块命名应描述领域职责。
- 不要把所有实现集中到单一文件；保持 Web、Agent、工具和持久化层的边界。
- **依赖方向由 `tests/test_architecture.py` 强制**，各包允许依赖谁见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 3 节。新增包时先在该测试的 `FORBIDDEN_IMPORTS` 里声明规则，再写实现。
- 横切关注点只从固定入口进入：配置走 `core/config.py`，模型目录走 `core/model_catalog.py`，模型实例走 `core/chat_model.py`，MCP 走 `core/mcp_config.py`，长期记忆走 `memory/MemoryService`，业务数据读写走 `repository/`。不要另开旁路。
- 依赖以 `pyproject.toml` 和 `uv.lock` 为准，使用 `uv sync` 管理环境；`requirements.txt` 仅作为 pip 兼容清单维护。
- **不为数据库历史数据写兼容代码。** 这是开发期项目，允许清空存量数据库：能通过清空数据库简化设计时，就清空数据库并简化设计。表结构只改 `database/schema.py`，由 `uv run melonclaw-db-init` 的 `metadata.create_all` 一次性建表；不写 `ADD COLUMN IF NOT EXISTS` 迁移、不维护 `schema_migrations` 版本常量、不在读取路径上为“老数据可能没有这个字段”写 `or` / `hasattr` 兜底。发现这类代码应直接删除，而不是继续扩展它。

## 配置与可移植性

- 不写入个人机器的绝对路径、用户名、数据库账号或本地密钥。项目根目录使用 `Path(__file__).resolve()` 或明确的配置项推导。
- 外部服务地址、模型、数据库、工作区和 Web 监听参数通过环境变量或根目录配置文件提供。
- `mcp.json` 保存内置 MCP 种子，`melonclaw-db-init` 把它单向同步进数据库，运行时只读数据库（`services/mcp.py` 装配 MCP，`skills` 表索引 Skill）。系统级 Skill 的唯一存储是 `MELONCLAW_DATA_DIR`（默认 `.data/`）下的 `skills/shared/`，新增/更新走资源管理 UI 或 skill_import 服务；`shared/` 正文纳入版本控制（`cicc-*`/`htsc-*` 除外），`users/`、`tmp/` 只保留空目录占位不进版本控制。`.env` 只保存凭据和占位符值。MCP 配置必须支持无 MCP 时正常启动。
- 新增持久化路径时，区分临时 runtime、Web Project 持久 workspace、项目源文件和数据库数据，避免把用户数据写进仓库。
- 保持跨用户运行所需的相对路径和用户目录默认值；不要假设仓库位于某个固定用户名目录下。

## Agent 能力与安全边界

- 文件写入、删除、Shell 执行、外部写操作和其他有副作用的工具必须明确经过 HITL 或受控权限边界。
- PTC/Interpreter 只允许加入已经确认无需逐次审批且副作用明确受限的工具；不能因为主 Agent 配置了 HITL 就把写文件、Shell、数据库写入、发消息、交易或部署工具放入 PTC。
- 任何新增工具都要说明输入校验、权限范围、错误处理、敏感信息脱敏和是否进入审批清单；进入审批清单的工具在 `core/hitl.py` 中登记，不进清单的要写明理由。
- Web 的 `user_id` 是开发模拟身份；服务层必须从用户记录解析唯一且有效的租户归属，并重新校验 Project、Conversation 和附件归属。Conversation 只归属 `user_id + project_id`，`tenant_id` 由服务端解析后用于 Agent 和 Memory 运行上下文。开发模拟用户不能被描述成生产认证系统。初次上线只有 `system` 租户下的 `admin`（种子写入）；其他用户由 admin 通过 `POST /api/dev/users` 创建（系统租户 member），服务端校验 admin/owner 身份。
- 当前 `LocalShellBackend` 不是安全沙箱。若新增面向共享环境的能力，必须说明隔离方案、授权边界和部署限制。
- 不依赖上游框架按类型自动推断模型能力。涉及多模态、文件类型、工具权限的判断必须由本仓库显式声明，原因见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 5.2 节。

## 文档与验证

- 用户说明书 Skill `melonclaw-tutorial` 仅在用户显式要求根据新增或变化的项目功能更新时才修改；不要因功能变化主动更新该 Skill。此约定不影响 README.md 和 docs/ 的同步维护要求。`.data/skills/shared/melonclaw-tutorial/` 及其内容纳入 Git 版本管理，不得被 `.gitignore` 忽略。
- 新功能的设计分析先写入 `note/note.md`，可长期复用的结论搬进 `docs/design-docs/` 并更新索引；至少包含背景与目标、方案概览、关键设计选择、数据/事件流、失败与安全边界、运行步骤、预期结果和验证记录。
- 新增命令、环境变量、API、MCP 服务或用户可见行为时，同步更新 README.md；架构、依赖边界或横切入口变化时，同步更新 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 修改依赖、数据库结构或运行入口后，给出简洁的初始化/运行命令和验证结果。服务启动不会建表也不会迁移，建表必须执行 `uv run melonclaw-db-init`。
- 提交前运行 `scripts/check.sh`（后端编译、测试、lint，前端 lint、类型检查、测试，文档链接校验）。前端依赖未安装时脚本会跳过前端部分。
- 需要真实外部 API、数据库或 MCP 服务的验证，应明确依赖和观察点；不要在日志或回复中暴露凭据。

## 检查命令

```bash
scripts/check.sh              # 全部（推荐）
scripts/check.sh backend      # 只跑后端
scripts/check.sh frontend     # 只跑前端
```

等价的手工命令：

```bash
uv run python -m compileall -q src
uv run pytest -q tests        # pytest 与 ruff 声明在 pyproject.toml 的 dev 依赖分组中
uv run ruff check src tests
uv lock --check
git diff --check
cd frontend && npm run lint && npm run typecheck && npm test
```

新增或调整检查项时，同时改三处：`scripts/check.sh`、`.github/workflows/check.yml` 和本节。更多环境陷阱见 [docs/QUALITY_SCORE.md](docs/QUALITY_SCORE.md)。
