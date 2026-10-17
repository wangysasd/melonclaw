# MelonClaw 代码清理审计与实施记录

状态：已完成（用户明确结束本轮审计及清理；其余候选不继续实施）
开始日期：2026-10-17
审计日期：2026-10-17（北京时间）
结束日期：2026-10-17

## 结束说明

本轮已结束：A 类 CLN-001～013/024/025、B 类 CLN-017/015/036 及文档 CLN-035 已完成。其余 5 项 B 类和 14 项 C 类保留为审计证据，见[技术债后续评估](../tech-debt-tracker.md#审计结束后的独立评估记录)，不继续自动执行。下文的“下一轮”“建议”“待验证”均指当时的审计判断，不是本轮未完成步骤。

本次生成的临时 diff、离线验证脚本、日志、OpenAPI 快照、临时测试目录和隔离安装环境已清理。源码差异仍保留在工作区，未提交、推送或部署。此前被删除的 note 原稿未纳入 Git，因此另保留一份仓库外恢复备份；不清理应用依赖、运行数据或其他任务的临时文件。

## 目标

减少无用代码、重复实现和维护负担，同时保持现有功能、HTTP/SSE 接口、业务数据、Checkpoint、权限及取消/恢复行为。审计轮只交付报告；随后用户明确授权实施首批证据充分的 A 类。本次实施范围与结果见「首批实施记录」，B/C 类不实施。

## 背景与审计工作区基线（历史记录）

- 分支：`main`。
- HEAD：`df1587a76c15f49434872aa8e50d1a5a8b936a62`（`scheduler auth`）。以下行号均对应这个 HEAD，后续实施前须重新定位。
- 开始时 `git status --short` 输出为空，无用户未提交改动。未切分支、提交、重置或暂存文件。
- 已阅读 [AGENTS.md](../../../AGENTS.md)、[架构](../../ARCHITECTURE.md)、[前端](../../FRONTEND.md)、[质量评分](../../QUALITY_SCORE.md)、[技术债](../tech-debt-tracker.md)与[执行计划约定](../README.md)。针对候选补读任务可靠性、任务设计/验收及相关实现。
- 审计轮仅新增本文件，未修改业务源码、测试、依赖、锁文件、运行配置或其他文档。后续首批实施单独记在下节，初始审计记录不覆盖。检查产生的忽略目录内缓存、前端构建产物和系统临时文件不属于交付。
- 未运行服务启动/停止/部署脚本、db-init/db-update 命令，未连接业务数据库、清理用户文件/历史或触发真实任务。数据库升级仅阅读代码；真实数据库专项测试因没有测试连接而跳过。

## 步骤

- [x] 记录分支、HEAD、状态，阅读约定和设计地图。
- [x] 核对近期 Git 历史与当前任务、消息、权限、升级链路。
- [x] 对指定目录进行静态检索、引用/注册核查和有针对性的实现阅读。
- [x] 运行现有全量检查与前端生产构建，补充检查跳过原因和模板脚本告警。
- [x] 分别记录死代码、重复逻辑、性能和文档候选，给出风险、置信度、实施顺序和回退方法。
- [x] 用户授权后重新核对 HEAD、工作区与引用关系，按目的实施 CLN-001～013、024、025。
- [x] 每批记录验证结果，完成最终检查和可审阅 diff。
- [x] 按用户要求结束本轮审计，归档已实施结果；未实施候选转为技术债中的独立评估记录，不等待它们完成。

## B 类指定批次实施（2026-10-17）

用户明确授权按 CLN-017 → CLN-015 → CLN-036 顺序实施。开始时 main/HEAD 仍为 df1587a76c15f49434872aa8e50d1a5a8b936a62；此前 A 类源码清理与 docs/note 整理均尚未提交，本轮已保存指定文件的开始副本，不覆盖已有差异。其他 B/C 候选不实施。

- [x] CLN-017：按现有锁文件重新导出 requirements，保持 pyproject/uv.lock 不变；复导出与适用平台版本/导入校验通过。
- [x] CLN-015：复用现有就绪等待函数，离线验证成功、重试、超时与仅前端分支。
- [x] CLN-036：收紧 API 导入例外并对齐五个命令入口说明，验证反向导入仍被拒绝。
- [x] 最终运行完整检查和前端构建，保存本轮专用 diff 与回退说明。

### 指定 B 类变更与分批结果

| 顺序/ID | 文件及实际变更 | 验证结果 |
|---|---|---|
| 1 / CLN-017 | requirements.txt 使用 uv export --locked --no-hashes --no-dev --no-emit-project 重新生成；补齐锁内已有 pandas/playwright/pycryptodome 及 numpy/pyee/python-dateutil/six，保留 uv 生成的平台标记与来源注释 | 二次锁定导出除命令路径注释外一致；uv lock --check 通过；137 个当前平台适用 pin 与 uv 环境版本一致；临时虚拟环境 uv pip sync --offline 成功，uv pip check 与 pandas/requests/Crypto/playwright/fastmcp 离线导入通过。pyproject.toml、uv.lock 字节未变；没有下载浏览器或执行外部 Skill。传统 pip 安装器、其他 OS/Python 分支及外部 Skill 运行未验证 |
| 2 / CLN-015 | scripts/start.sh 后端就绪检测调用既有 wait_for，删除重复 wait_for_backend；/api/ready URL、参数顺序、curl 选项、30 秒次数限制及成功/失败处理不变 | bash -n 通过；对修改前后的完整脚本离线模拟 7 场景：立即成功、仅前端、后端重试、前端重试、后端超时、前端超时、仅前端超时。stdout/stderr、退出码、URL/请求参数、尝试次数与 sleep 次数逐项相同；nohup/curl/sleep/lsof 全部替身，不启动服务、不联网、不读实际配置。补正初始文字：等待函数参数实际为 URL、名称、超时；超时 echo 写 stdout，本次均保留 |
| 3 / CLN-036 | tests/test_architecture.py 将 ENTRY_FILES 改为 API_ENTRY_FILES，仅允许包根 main_web.py；错误提示同步。docs/ARCHITECTURE.md 分开 Web/管理入口并列五个命令 | 架构与文档链接合计 7 passed，定向 ruff 通过；8 个临时正反探针确认 Web/API 内部允许、四个管理 CLI/业务模块/嵌套同名 main_web 均拒绝。五个实际注册模块的 AST 导入符合约束；未调用 CLI，未放宽规则 |

### 本轮验收与回退

最终 scripts/check.sh 退出码 0：compileall、pytest、ruff、uv lock --check、git diff --check、前端 eslint/tsc/vitest 全部通过，前端依赖已安装且未跳过。npm --prefix frontend run build 退出码 0，Vite 转换 6904 模块、构建 6.43 秒，原有 >500 kB 分块提示保留。没有普通断言失败；脚本隐藏详细用例输出，本轮未重新提取全量计数。专用数据库连接仍未配置，相关集成测试未验证；已有后台流 strict xfail 保留，首次审计/首批 A 类的 59 skipped 与 2 xfailed 记录仍是各自日期实测。此处不以静态通过替代真实数据库、服务器或外部 API 验证。保留前一轮源码和文档清理，其他 B/C 项仍不实施；未提交、暂存、推送或部署，未执行数据库初始化/升级、密码重置、真实任务或用户数据操作。

本轮补丁单独包含 requirements.txt、scripts/start.sh、tests/test_architecture.py、docs/ARCHITECTURE.md 的新增 hunks 及本报告相对开始副本的更新。回退只逆向对应 hunks，不能整文件恢复架构文档而覆盖前轮整理。requirements 是现有锁文件的生成清单，回退不需要改锁或重建数据库；离线验证环境只在系统临时目录。

## 后续文档整理说明（2026-10-17）

用户另行授权整理 docs/note，仅保留当前实现说明。该轮只改文档，详细删除/合并清单见[文档整理记录](2026-10-17-documentation-cleanup.md)。CLN-035 的旧供应商执行计划已由当前模型设计承接并删除；CLN-037 的页脚展示误述已修正，闲置组件及测试继续保留，C 类去留没有实施。前文源文件行号、初始文档事实与验证输出均是对应审计时点记录，不作为整理后的现状描述。本清理报告保留用于追溯已实施 A 项和现存 B/C 候选，不把待决候选宣传为已实现功能。

## 首批实施记录（2026-10-17）

用户授权：实施证据充分且保持行为的 A 类，无需逐文件确认；B/C 类保留。本轮实施开始的分支为 `main`，HEAD 仍为 `df1587a76c15f49434872aa8e50d1a5a8b936a62`，工作区只有此前未跟踪的本报告；报告修改前另留临时副本用于生成精确差异。没有其他用户源码改动需要搬移，未暂存、提交、推送或部署。

重新检索仓库源码、测试、脚本、模板与注册后，原 A 类证据仍成立，本轮没有新增降级项。原 CLN-026 等涉及参数/权限/事务/消息协议的 C 类，以及全部 B 类，保持原记录。以下历史候选位置仍对应审计 HEAD，删除后的行号不再作为当前定位依据。

| 独立批次 | ID | 修改文件与实际改动 | 清理依据 |
|---|---|---|---|
| 后端私有死代码 | CLN-002 | `src/melonclaw/output/formatting.py` 删除 _call_key | 旧适配器消费者已退场，无注册/副作用 |
| 后端私有死代码 | CLN-003 | `src/melonclaw/services/chat.py` 删除三个私有转发方法 | 当前消费者直接使用 runtime，公开服务门面保留 |
| 后端私有死代码 | CLN-004 | `src/melonclaw/services/resource_service.py` 删除 _skill_dir | 无调用，受控 _checked_skill_dir 保留 |
| 后端静态声明 | CLN-024 | `chat.py` 删除 USER_ID_RE/re；`output/assistant_steps.py` 删除 STEP_STATUSES；`services/skills.py` 删除 MAX_SKILL_NAME_LENGTH/SKILL_SCOPES | 精确符号仅有声明，无导出/注册消费者；64 字符规则和 TOOL_STATUSES 保留 |
| 前端孤立导出 | CLN-005 | `frontend/src/lib/format.ts` 删除 safeDate | 无消费者，活跃日期解析/格式函数保留 |
| 前端孤立导出 | CLN-006 | `frontend/src/hooks/useChatStream.ts` 删除 ChatDispatch 和 Dispatch 类型 import | 纯类型无消费者，不调整 reducer/SSE |
| 前端孤立导出 | CLN-007 | `frontend/src/state/storage.ts` 删除 USER_STORAGE_KEY | Cookie 已接管身份；未操作浏览器值 |
| 前端旧 Hook | CLN-025 | 删除 `frontend/src/hooks/useServiceStatus.ts`；`state/session.tsx` 删除 deriveRunStatus；`frontend/tests/sidebar-actions.test.tsx` 仅删失效 mock | 完整链路只剩失效测试 mock；所有测试用例/断言保留，runStatus/setRunStatus 保留 |
| 分发模板 | CLN-001 | `.data/skills/shared/tushare-fetcher/scripts/tsdoc.py` 删除局部 d 赋值 | 仅常量求值且未使用，参数默认值不变 |
| 分发模板 | CLN-013 | `.data/skills/shared/tushare-fetcher/scripts/tsfetch.py` 删除两处冗余 f 前缀 | 无插值表达式，零行分支及 return 5 保留 |
| 孤立 API 模型 | CLN-008 | `src/melonclaw/api/schemas.py` 删除两个 Assistant*Schema | 无请求/响应/注册消费者，完整 OpenAPI 前后等价 |
| 身份文档 | CLN-009 | `docs/ARCHITECTURE.md` 修正 Cookie 身份、断言、开发切换与用户租户编辑说明 | 与全局 require_session、accounts 管理路由及当前服务归属解析一致 |
| 质量与债务文档 | CLN-010 | `docs/QUALITY_SCORE.md`、`docs/exec-plans/tech-debt-tracker.md` 修正统计及 D2/D3/D6/D8/D9/D10 事实 | 标明审计/实施验证日期，CI/生产/草稿迁移未知状态保留 |
| 展示/部署说明 | CLN-011 | `docs/FRONTEND.md` 修正高亮/审批描述与重复编号；`ops/DEPLOY.md`、`ops/deploy.sh` 修正通知验收/成功提示 | 代码高亮沿用组件默认浅色；通知已移除；部署脚本只改 echo 文本 |
| 停止流注释 | CLN-012 | `frontend/src/hooks/useChatStream.ts` 修正 stopConversation 注释 | 区分本地中止订阅和服务端继续执行，未改取消实现 |

### 分批验证与最终验收

| 批次/检查 | 实测结果 | 观察边界 |
|---|---|---|
| CLN-002/003/004/024 | 后端 ruff 通过；13 个专项文件合计 115 passed、17 subtests passed、2 warnings | assistant steps、并行工具流、内容流、脱敏、准备/移动、Skill 安装/生命周期/导入/资源部署；警告为现有 LangGraph beta |
| CLN-008 | 删除前后 create_app().openapi() 完整对象相等；5 个专项文件 41 passed | 未进入 lifespan/启动服务；账户 API、assistant steps、后台流、执行轨迹、schema |
| CLN-005/006/007/012/025 | 前端 lint/typecheck 通过；7 个专项文件 77 passed | 保留普通流、任务投递、认证切换、新会话、导航和侧栏断言；jsdom getComputedStyle 与 antd 提示仍出现，未通过改测试规避 |
| CLN-001/013 | 模板 `ruff --select F` 通过；80 组离线 CLI namespace/输出等价；空结果 stdout/stderr/返回码 5 等价 | 选项置于子命令前/后、五种子命令输入及三个全局开关组合；stub 文档/数据返回，无联网、无落盘 |
| CLN-009/010/011 | docs_links + deploy_script 合计 7 passed；bash -n ops/deploy.sh 通过 | 未执行部署或真实任务；脚本只改输出文案 |
| 剩余后端实现等价 | 六个改动 Python 模块去除指定声明后的 AST 与当前 AST 相等 | 其余函数体、控制流、调用及返回值无变更；不能单独替代调用证明 |
| 最终 scripts/check.sh | 退出码 0：compileall、pytest、ruff、uv lock --check、git diff --check、eslint、tsc、vitest 全部通过 | 前端依赖已安装，未跳过前端；保持原规则 |
| 最终详细 pytest 汇总 | 664 passed、59 skipped、2 xfailed、27 subtests passed、3 warnings，15.72 秒 | 59 项均缺专用 PostgreSQL 连接，未验证；2 strict xfail 为既有后台流缺口，均保留；不借业务数据库 |
| 最终 frontend build | 退出码 0；Vite 6904 模块，7.36 秒 | 两 index 块与审计基线相同，>500 kB 提示保留；不承诺包体积或性能收益 |

未处理：所有 B 类（CLN-014～018、028、031、035、036）与 C 类（CLN-019～023、026、027、029、030、032～034、037、038）；未删除对应测试，未改依赖/锁、数据库升级、配置或用户资源。本轮没有扩大普通会话正文轮询、合并权限或抽取大型模块。

### 审阅与回退

所有改动保留为工作区差异，按上表目的审阅；本报告仍保留此前未跟踪状态。tracked diff 与本报告相对实施前副本的差异一起导出为仓库外临时补丁，供审阅/按 hunks 回退；不将临时补丁作为仓库产物。未自动提交。未提交状态下仅逆向应用对应批次 hunks，先检查补丁与当前内容匹配；不得整树 reset 或覆盖整个路径。之后若用户独立提交，再按对应提交 revert。无 schema/用户数据变更，无需数据库回滚；分发模板的撤回不触及已安装副本。

## 检索方法、覆盖范围与限制

### 范围 S1：仓库调用与声明

以 Git 跟踪文件清单为主，覆盖 `src/melonclaw/`（185 个跟踪文件）、`frontend/src/`（113 个）、`tests/`、`frontend/tests/`（50 个）、`scripts/`、`ops/`、`example/`、`pyproject.toml`、`uv.lock`、`requirements.txt`、前端依赖及配置、`.github/workflows/`、README、docs 和 `.data/skills/shared/` 的 11 个内置分发文件。核查 Python 定义/import/re-export、TS 导出/导入、JSX、字符串名、reducer action、CSS 动态来源和文档引用。关键候选另做隐藏文件检索，并排除运行目录和敏感配置内容。

未把 `.venv/`、`node_modules/`、`dist/`、缓存、`note/` 草稿或用户运行数据作为源码扫描对象。依赖已安装性及锁文件依赖图可以用于基线判断；前端构建输出用于记录实际构建警告，不作为源码使用证明。未输出 `.env`、实际凭据或完整凭据 URL。

### 范围 S2：动态入口与特殊路径

人工核查 FastAPI 应用组装/路由装饰器/依赖/生命周期、Pydantic 注册、五个命令入口、APScheduler 注册和 done callback、Agent 主/子/后台组装、工具 provider 与目录、middleware/callback 钩子、MCP 发现与白名单、Skill 目录和脚本命令。检查正常、失败、取消、幂等重放及重启/恢复链路中与候选有关的入口。

Python AST 与标识符出现次数仅用来找线索。初次前端文件路径扫描把测试中的 mock 也计作引用；补充复核发现 `useServiceStatus` 的唯一外部引用是失效 mock，实际运行链已退出，修正为 CLN-025。部分单次出现符号实际是框架回调，已排除。没有对每条运行路径做动态覆盖证明，未确认的候选进入 C 类。

### 范围 S3：相关历史

查看最近 25 条提交，并针对候选做 `git log -S`、路径历史和补丁核对：

| 提交 | 当前实现核对结果 |
|---|---|
| `df1587a`（2026-10-16） | system/admin 的任务列表可跨所有者概览；运行、编辑、记录和会话仍按所有者/当前租户校验。不能把列表条件统一成普通 scope，也不能把概览权限推广到详情操作。 |
| `c76712d`（2026-10-16） | 文件写/编辑/删除移出逐次审批清单，仍保留路径权限；主/子 Agent 指引同步。旧的注释掉 execute 配置已删除。 |
| `48200b3`（2026-10-16） | 已授权 MCP 调用免逐次审批，安装/测试继续审批；工具预算及提示词调整。不能按旧设计恢复已取消的审批行为。 |
| `6ac5085`（2026-10-15） | MCP 安装范围按真实角色；任务摘要刷新沿列表查询先筛选再分页；新增 requests/pandas/pycryptodome/playwright 直接依赖并更新 uv.lock，但 requirements 导出未同步。 |
| `1a85876`（2026-10-15） | 当前任务中心、固定会话、连续历史、受控配置、通知/引用清理与可靠性升级已落入代码；不是待重做的旧计划。 |
| `77e4495`（2026-10-06） | Cookie 登录进入全局依赖，切用户改为服务端 switch + AuthGate 重建 Provider；旧 localStorage 身份写入及部分 action 派发被移除。 |
| `d8b1587`、`0375724` | 前者移除旧事件适配器及其 `_call_key` 调用；后者引入该辅助函数。当前仅辅助函数定义残留。 |

### 未验证项目

生产日志、线上 CI 运行/分支保护历史、实际服务器配置、业务数据库 schema/数据、Linux systemd/Nginx/logrotate 实机行为、外部 MCP/模型/API、浏览器端到端流程、两种品牌实际页面和性能网络瀑布均为**未验证**。读取仓库 CI/部署模板不等于读取这些实际环境。

仓库外已安装 Skill 正文及脚本属于用户运行数据，本轮未读取，也未据此建立完整依赖清单。依赖于这些内容的裁剪一律待确认。`note/` 中的历史记录不作为事实证据。本文所称“无调用”限于 S1/S2 已检查的仓库路径，不宣称排除外部 Python 包使用者。

## 验证基线

以下是首次审计历史记录；首批实施后的最新结果见前面的实施记录。

| 检查 | 本轮结果 | 边界/观察点 |
|---|---|---|
| `scripts/check.sh` | 退出码 0 | compileall、pytest、ruff、uv lock --check、git diff --check、前端 eslint/tsc/vitest 均通过。前端 node_modules 存在，没有跳过前端。脚本成功时隐藏详细测试输出，因此另读 pytest 的跳过汇总。 |
| `uv run pytest -q -rs tests` | 664 passed、59 skipped、2 xfailed、27 subtests passed、3 warnings | 所有 59 项跳过均要求 `MELONCLAW_TEST_DATABASE_URL`；当前未提供。没有借用业务连接，也未尝试从业务配置提取连接。 |
| 2 项 xfail | 原有、strict=True | `tests/test_review_background_stream.py:83` 的 producer 异常未取回；`:146` 的首次迭代前关闭无法执行清理。是已知缺口，不能删除测试来消除报告数字。 |
| 后端警告 | 原有 beta 警告 | LangGraph v3 streaming 和 CodeInterpreterMiddleware 的实验/beta 提示；不是本轮引入的失败。 |
| `npm --prefix frontend run build` | 退出码 0 | Vite 5.4.21，6904 模块转换，构建输出显示 8.01 秒；这只是一次本机记录，不是 CI 或性能收益。 |
| 构建分块提示 | 原有问题仍可复现 | index 两块约 1482.71/1558.54 kB（gzip 481.14/524.34 kB）；ResultChartCanvas 546.70 kB，MermaidDiagram 674.87 kB，cynefin 690.59 kB，存在 >500 kB 警告。未测首屏实际请求/缓存/耗时。 |
| `uv run ruff check --select TRY,BLE src tests --statistics` | 退出码非零，435 个扩展规则告警 | TRY003 320、TRY301 54、BLE001 28、TRY004 19、TRY300 13、TRY203 1。这些规则不在常规 select 中，不等于常规 lint 失败，也不等于 435 个清理项。 |
| `uv run ruff check scripts example .data/skills/shared` | 退出码 1，7 个扩展范围告警 | F841 ×1、F541 ×2、I001 ×4；常规脚本的后端 lint 只覆盖 src/tests，不含示例/模板。告警详见 CLN-001/013。 |
| 锁文件只读导出比较 | 已验证有漂移 | 导出到系统临时目录，再与 requirements 比较；未改 requirements/uv.lock/pyproject。详见 CLN-017。 |
| OpenAPI 静态生成 | 两个候选 schema 均未注册 | 调用 create_app().openapi()，未进入 lifespan、未启动执行器/数据库。详见 CLN-008。 |

没有普通测试断言失败。通过的检查不能单独证明代码未使用；跳过与 xfail 不算已验证通过。未复测历史文档所称“含真实 PostgreSQL 全部通过”。

## 分级与目录

实施状态以顶部记录为准：A 类已实施；B 类 CLN-017/015/036 及文档 CLN-035 已处理，其余 B 类 CLN-014/016/018/028/031 与全部 C 类继续保留。以下等级保留审计分类，不代表项目仍未处理。

A 表示低风险清理，本轮已实施项见首批记录；B 表示需要独立边界/验证方案的重复逻辑、模块或依赖调整；C 表示证据不足或涉及权限、事务、动态调用和消息协议，暂不删除。**等级、风险、置信度分开**：高置信度的事实也可能只支持“保留”，而不能支持删除。

| ID | 等级 | 清理类别 | 风险 | 置信度（事实 / 建议） |
|---|---|---|---|---|
| CLN-001 | A | 删除死代码：模板局部变量 | 低 | 高 / 高 |
| CLN-002 | A | 删除死代码：旧事件辅助函数 | 低 | 高 / 高 |
| CLN-003 | A | 删除死代码：私有转发方法 | 低 | 高 / 高 |
| CLN-004 | A | 删除死代码：无调用路径助手 | 低 | 高 / 高 |
| CLN-005 | A | 删除死代码：日期导出包装 | 低 | 高 / 高 |
| CLN-006 | A | 删除死代码：TS 类型导出 | 低 | 高 / 高 |
| CLN-007 | A | 删除死代码：旧身份存储常量 | 低 | 高 / 高 |
| CLN-008 | A | 删除死代码：未注册响应模型 | 低 | 高 / 高（仓库范围） |
| CLN-009 | A | 文档整理：旧身份描述 | 低 | 高 / 高 |
| CLN-010 | A | 文档整理：质量/技术债过期事实 | 低 | 高 / 高（限定已核实事实） |
| CLN-011 | A | 文档整理：重复/过时展示及部署文案 | 低 | 高 / 高 |
| CLN-012 | A | 文档整理：停止流注释 | 低 | 高 / 高（只改注释） |
| CLN-013 | A | 删除死代码：无插值 f 前缀 | 低 | 高 / 高 |
| CLN-014 | B | 重构重复逻辑：可见性轮询 | 中 | 高 / 中 |
| CLN-015 | B | 重构重复逻辑：启动就绪等待 | 低 | 高 / 高 |
| CLN-016 | B | 重构职责：大模块拆分 | 中至高 | 高 / 中 |
| CLN-017 | B | 依赖清单整理：生成文件漂移 | 中 | 高 / 高 |
| CLN-018 | B | 性能优化：首屏依赖分析 | 中 | 高 / 中（收益未测） |
| CLN-019 | C | 直接依赖裁剪待确认 | 中至高 | 高 / 低（删除） |
| CLN-020 | C | 删除死代码候选：未派发 action | 高 | 高 / 中（删除） |
| CLN-021 | C | 删除死代码候选：公开未调用方法 | 中至高 | 高 / 低（删除） |
| CLN-022 | C | 删除死代码候选：旧工具审批展示 | 高 | 高 / 低（删除） |
| CLN-023 | C | 删除死资源候选：旧 README 品牌图 | 中 | 高 / 低（删除） |
| CLN-024 | A | 删除死代码：未使用常量及关联 import | 低 | 高 / 高 |
| CLN-025 | A | 删除死代码：旧状态 Hook 与失效 mock | 低 | 高 / 高 |
| CLN-026 | C | 删除死参数候选：流、工具装配与权限 helper | 中–高 | 高 / 中 |
| CLN-027 | C | 删除死代码候选：显式公开导出 | 中 | 高 / 低（删除） |
| CLN-028 | B | 删除死代码候选：仅测试使用的前端摘要/解析包装 | 中 | 高 / 中 |
| CLN-029 | C | 删除死代码候选：公开 MCP loader | 高 | 高 / 低（删除） |
| CLN-030 | C | 删除死状态候选：全局 runStatus | 高 | 高 / 中 |
| CLN-031 | B | 重构重复逻辑：UTC 时钟 helper | 中 | 高 / 中 |
| CLN-032 | C | 重构重复逻辑：交互账本复用判断 | 高 | 高 / 中 |
| CLN-033 | C | 重构重复逻辑候选：密码命令环境加载 | 高 | 高 / 低（等价删除） |
| CLN-034 | C | 重构重复逻辑候选：聊天安装身份核验 | 高 | 高 / 低（直接合并） |
| CLN-035 | B | 文档整理：旧供应商计划归档与历史标注 | 低 | 高 / 中（完成认定） |
| CLN-036 | B | 重构重复配置：API 导入例外与入口文档对齐 | 中 | 高 / 中 |
| CLN-037 | C | 删除死代码候选：未挂载的合规声明 | 中–高 | 高 / 低（删除） |
| CLN-038 | C | 删除死资源候选：两项图标名 | 中 | 高 / 低（删除） |

没有把文件长度、未启用状态或单次字符串出现直接当作删除理由。重复逻辑与性能项不纳入“死代码收益”。

## A 类候选明细

### CLN-001：模板脚本的未使用局部变量

- 类别、位置：删除死代码；`.data/skills/shared/tushare-fetcher/scripts/tsdoc.py:512`，`main.common_parser` 的 `d`（函数始于 510）。
- 事实：`d = argparse.SUPPRESS if suppress else None` 之后未被读取；各 add_argument 已自行使用 suppress 决定 default。Ruff F841 可复现。
- 引用/注册证据与检索范围：S1/S2；SKILL.md:67/70/73 等实际通过 CLI 调用本脚本，模板和 reference 同样引用它。此脚本不是死文件，只有该局部赋值没有消费者。赋值仅求值 argparse 常量/None，无 I/O 或注册副作用。
- 尚未排除调用：未检查运行数据根中的已安装副本；局部变量没有对外导出或回调引用。只能改仓库分发模板，不能覆盖用户副本。
- 风险/置信度/影响面：低；事实高、删除高；仅分发脚本源码，参数默认值保持。
- 建议：只删除该行，不改 suppress 规则、不执行 check/doc/search 的联网操作。
- 验证：模板目录静态检查；对全局选项置于子命令前/后的 argparse 解析用离线输入核对 namespace，保持 SUPPRESS 语义；常规检查。现有应用测试未覆盖这个变量，无测试需要删除。
- 预期收益：去掉一处真实无用赋值和 F841 告警；不承诺性能收益。

### CLN-002：旧流适配器残留 `_call_key`

- 类别、位置：删除死代码；`src/melonclaw/output/formatting.py:77`，`_call_key`。
- 事实：当前仓库只有定义。`d8b1587` 删除 output/events.py 的 import 和两个旧适配器调用；当前事件处理统一走 v3/assistant step projector。
- 引用/注册证据与检索范围：S1/S2/S3；output/__init__.py 仅导出 content_to_text、iter_research_events、sanitize_text；函数无装饰器、无 import 副作用、无工具/回调/字符串注册。当前 `_decode_tool_args`、`_preview` 仍有调用，不能连带删除。
- 尚未排除调用：仓库外对私有符号的非约定导入未验证；仓库动态加载、测试、部署、Skill 与脚本未见此名字。
- 风险/置信度/影响面：低；高/高；仅旧适配器内部助手，不触及当前消息去重。
- 建议：删除整个函数，保留当前 v3、call_id/step_id、脱敏和结果解析实现。
- 验证：ruff/compileall；assistant steps、parallel tool stream、tool-call content stream、模型活动与脱敏测试；完整 check.sh。无需删除既有测试。
- 预期收益：消除旧协议残影，减少维护时误用过时关联规则的机会。

### CLN-003：ChatService 三个退出调用链的私有转发方法

- 类别、位置：删除死代码；`src/melonclaw/services/chat.py:390` `_require_ready`、`:393` `_project_workspace_dir`、`:397` `_conversation_config`（staticmethod 装饰器在 396）。
- 事实：每个方法仅转发到 runtime/ChatRuntime；当前源码和测试没有调用。ChatService 其他公开门面方法仍被 API/安装 provider 使用，不能整批删除门面。
- 引用/注册证据与检索范围：S1/S2；ChatService 无父类/属性反射分发，这三项未被 __all__、FastAPI、provider 协议、scheduler 或 hook 注册。当前执行、会话服务直接调用 runtime 的实际实现。
- 尚未排除调用：外部对私有接口的直接访问未验证；仓库脚本、示例、Skill 和恢复路径无引用。
- 风险/置信度/影响面：低；高/高；不会删除 runtime 的就绪校验、工作区解析或 Checkpoint config。
- 建议：仅删三项方法，不新增转发层，不删 BusinessRepository/UUID/ChatRuntime 等仍被使用的 import。
- 验证：执行准备、历史加载、会话移动、后台流和聊天安装专项，加完整 check.sh；确认 API 仍调用公开门面，成功/失败/恢复锁路径不变。
- 预期收益：减少三个无职责入口，明确实际实现所在位置。

### CLN-004：无调用的 `_skill_dir`

- 类别、位置：删除死代码；`src/melonclaw/services/resource_service.py:211`，`ResourceService._skill_dir`。
- 事实：只做 data_root/skills/storage_path 拼接，无调用。下载及预览当前使用 `_checked_skill_dir`（214，调用在 294/397），包含路径/符号链接约束。
- 引用/注册证据与检索范围：S1/S2；普通类私有方法，无继承动态 hook 或字符串注册；Skill 管理和恢复代码未引用此项。
- 尚未排除调用：仓库外私有访问未验证；不要把 runtime._skill_dirs（不同符号、有调用）视为同项。
- 风险/置信度/影响面：低；高/高；只有不用的路径拼接方法。
- 建议：删 `_skill_dir`，完整保留受控解析、权限与已安装资源处理，不能用简单拼接替代 checked 方法。
- 验证：Skill 生命周期/导入/远程/持久路径和资源测试、完整 check.sh；没有测试删除需求。
- 预期收益：减少绕开安全路径校验的误用入口，不声称本轮修复已发生的权限漏洞。

### CLN-005：无消费者的日期包装导出

- 类别、位置：删除死代码；`frontend/src/lib/format.ts:15`，`safeDate`。
- 事实：只转发私有 toDate，S1 中仅导出定义；当前两个时间格式函数继续直接使用 toDate。
- 引用/注册证据与检索范围：S1/S2；无 barrel 导出、动态 glob、namespace 访问、JSX/字符串使用；模块的其他导出被正常引用，不能删整个模块。
- 尚未排除调用：仓库外消费者未验证；前端 private 应用未声明对外库入口。
- 风险/置信度/影响面：低；高/高；一个无副作用导出，不改日期/时区行为。
- 建议：仅删 safeDate，不合并任务的北京时间展示函数。
- 验证：前端 lint/typecheck/test/build、侧栏时间相关用例；保留现有测试。
- 预期收益：减少无职责包装和导出面，无可量化运行收益。

### CLN-006：未使用的 `ChatDispatch` 类型

- 类别、位置：删除死代码；`frontend/src/hooks/useChatStream.ts:1551`，`ChatDispatch`；相应 `type Dispatch` import 在 12。
- 事实：导出只定义一次，Dispatch 只用于该类型；ChatAction 仍被 reducer 与流事件使用。
- 引用/注册证据与检索范围：S1/S2；无消费者/re-export，类型在运行时擦除，不参与 SSE 或 reducer action 名称注册。
- 尚未排除调用：仓库外类型消费者未验证；不涉及真实运行回调。
- 风险/置信度/影响面：低；高/高；纯 TS 声明。
- 建议：删类型及 Dispatch 类型 import，保留所有 ChatAction、reducer、epoch 与终态逻辑。
- 验证：前端 typecheck/lint/test/build；尤其流归约、增量同步用例不变，无需移除测试。
- 预期收益：减少过期导出与 import，不等于减小构建 JS 体积。

### CLN-007：旧浏览器身份存储常量

- 类别、位置：删除死代码；`frontend/src/state/storage.ts:2`，`USER_STORAGE_KEY`。
- 事实：名字和 `melonclaw.user_id.v3` 字面值仅在此处出现。`77e4495` 移除 session 对该常量的导入/写入；当前 Cookie/AuthGate 决定身份。
- 引用/注册证据与检索范围：S1/S2/S3；无计算 key、storage 事件或测试使用该键；会话/项目/模型偏好键仍被使用，AuthGate 的认证同步使用自己的事件/存储机制。
- 尚未排除调用：旧浏览器可能仍保有历史键值，是否存在未检查；这不构成常量的当前消费者，也不授权清理本地数据。
- 风险/置信度/影响面：低；高/高；仅删除定义，Cookie 和浏览器既有值均不动。
- 建议：仅删除常量；不要调用 localStorage.removeItem，不改变身份同步和切换流程。
- 验证：auth/session/新建会话相关前端回归、typecheck/build；不要删除相关权限测试。
- 预期收益：消除过时身份来源的误导。

### CLN-008：未接入响应链的两个 Pydantic 模型

- 类别、位置：删除死代码；`src/melonclaw/api/schemas.py:17` `AssistantToolCallSchema`、`:32` `AssistantStepSchema`。
- 事实：前者只被后者字段引用，后者无消费者；静态生成的 OpenAPI components 不含任意一个。它们没有自定义 validator/注册副作用，当前 snapshot 由 output/services 以字典投影并持久化。
- 引用/注册证据与检索范围：S1/S2；检查路由 response_model、返回注解、import/re-export、字符串/反射使用及测试/Skill，无绑定证据。不能把同名含义的前端 AssistantStep/AssistantToolCall 类型一起删掉。
- 尚未排除调用：仓库外 Python 导入者未验证；不把“未在 OpenAPI”单独作为删除依据。
- 风险/置信度/影响面：低；事实高、仓库内删除高；两个无请求/响应消费者的声明。
- 建议：一起删除孤立声明，不替换为新响应模型、不改 SSE 字段或业务存储格式。
- 验证：删除前后比较完整 OpenAPI 文档应等价；路由、assistant steps、历史/SSE 契约及完整 check.sh。不能删除这些当前协议的测试。
- 预期收益：减少与当前内容块、计时字段可能漂移的第二份无效 schema。

### CLN-009：架构文档的身份说明自相矛盾

- 类别、位置：文档整理；`docs/ARCHITECTURE.md:40/42` 的身份段落，与 224 起“用户管理与开发登录”及横切入口表对照。
- 事实：前段仍称请求可任意指定身份、可信头仅接入附件；当前 api/app.py:47 为全局 require_session 依赖，api/auth.py:42/59 校验 Cookie/断言，identity.py 只读取已校验身份。dev 显式切用户仍允许冒用，不应误写为生产安全认证。
- 引用/注册证据与检索范围：S1/S2/S3；README、AGENTS、账户路由/测试与当前登录设计支持统一 Cookie 边界；不是仅凭新文档推断。
- 尚未排除调用：线上是否运行此 HEAD 未验证，文档只描述本 HEAD；不能借文档修订改变授权规则。
- 风险/置信度/影响面：低；高/高；说明书事实修正。
- 建议：合并成一处当前身份说明，引用当前登录设计；保留 dev 全员切换与 LocalShellBackend 非沙箱限制。
- 验证：文档链接测试；逐句对照 require_session、authenticate 与 profile 门控，不执行真实登录。
- 预期收益：避免维护者按过时身份模型调整权限。

### CLN-010：质量评分与技术债的过期统计

- 类别、位置：文档整理；`docs/QUALITY_SCORE.md:5/11/12/13/14/41`；`docs/exec-plans/tech-debt-tracker.md:5/11/12/14/16/17/18`（D2/D3/D6/D8/D9/D10）。
- 事实：表头仍为 9 月核实，写 95 后端/156 前端用例、8 组架构规则与 8 条扩展 lint 告警；当前架构禁止规则有 10 组，本轮 pytest/扩展 ruff 结果见基线。D6 文件实测分别 730/862/857 行，均不超过 900。已有多份真实数据库隔离测试，D2 不能再统称“没有可重复测试”；但完整 Agent/Checkpoint/SSE 组合风险并未因此消失。
- 引用/注册证据与检索范围：S1/S2/S3；tests/test_architecture.py、账户/任务/产物 PostgreSQL fixtures、当前 test 与 lint 结果。D10 的注释配置本体已删除，TODO 与非沙箱事实仍存在。附件已有版本化设计，不能直接认为 note 中全部研究已迁移。
- 尚未排除调用/事实：前端本轮用例数量未单独提取，不能用旧 156 或最新历史文档数量冒充本轮数字；D8 原始草稿未读；D9 CI 历史/分支保护未验证；D7 线上可观测性未验证。
- 风险/置信度/影响面：低；已核实事实高/高，外部状态未知；只改现状/来源和时间，不“偿还”未验证债务。
- 建议：当前基线与历史记录分栏，写清 skipped/xfail；重写 D2/D3/D6/D10，D8/D9 保留待核实状态，不机械启用 TRY/BLE 或修改异常类型。
- 验证：文档链接和数值来源核查；历史实测日期保留，不用本轮本机检查代替 CI/生产验证。
- 预期收益：减少错误任务和重复调查，不承诺 CI 时间收益。

### CLN-011：重复 UI 文案与已删除通知的验收残留

- 类别、位置：文档整理；`docs/FRONTEND.md:94`（代码块配色）、`:119`（文件/Shell 审批表述）、`:122/199`（两个第 6 节）；`ops/DEPLOY.md:301`（未读通知）；`ops/deploy.sh:80`（成功提示中的通知）。
- 事实：FRONTEND 的浅蓝代码标题与后续深色代码块总体描述需要明确部位；审批说明未区分当前三种安装/测试审批与旧操作展示能力；部署验收仍要求未读通知，而当前通知 API/UI 已移除。重复的第 6 节编号是当前组织问题，历史设计记录仍按日期保留，不整批删除。
- 引用/注册证据与检索范围：S1/S2；当前主题/Markdown、hitl 清单、任务路由/导航和升级代码核对。ops/deploy.sh 仍由部署文档/测试使用，不是死脚本。
- 尚未排除调用：历史审批展示消费者未排除，见 CLN-022；这里只纠正文案，不删除其 UI。线上通知状态未验证，文档描述当前代码。
- 风险/置信度/影响面：低；高/高；文案与章节组织。成功提示修改属于运维脚本输出，不能改部署动作/就绪标准。
- 建议：整理章节编号与展示规范；通知验收替换为固定会话结果/运行记录/侧栏摘要，不要求创建真实任务作为本轮验证；改成功提示的过时对象。
- 验证：文档链接、部署脚本现有测试及静态语法检查；差异只能是文案，不执行 deploy.sh。
- 预期收益：减少无效验收和维护者对审批行为的误判。

### CLN-012：停止流的注释与服务端行为不一致

- 类别、位置：文档整理（代码注释）；`frontend/src/hooks/useChatStream.ts:1475`，stopConversation 前注释。
- 事实：注释称 abort 会让后端写 cancelled/request_cancelled；实现只 abort fetch 并乐观置本地 cancelled。`api/background_stream.py:17` persistent_stream 在订阅解除后让 producer 继续，只有 close_stream_tasks 会取消服务任务。相关断线持续执行测试仍存在。
- 引用/注册证据与检索范围：S1/S2；聊天/审批/用户输入路由用 persistent_stream；stopConversation 未调用服务端 cancel API，任务取消走独立 task-runs 路由。
- 尚未排除调用：浏览器实际点击后的全部体验未做 E2E；改实现属于行为调整，不能随清理执行。
- 风险/置信度/影响面：只改注释低；高/高；不影响现有取消、断线或恢复行为。
- 建议：注释准确区分“停止本地显示/订阅”和“后端业务执行继续”；若产品需要真正停止普通聊天，另建功能方案，不能在本项顺手修复。
- 验证：差异只有注释；后台流/终态对账回归继续保留，前端检查通过。
- 预期收益：防止后续重构错误地移除服务端持续执行或恢复分支。

### CLN-013：模板脚本两处无插值 f-string

- 类别、位置：删除死代码（冗余语法）；`.data/skills/shared/tushare-fetcher/scripts/tsfetch.py:563/564`，`_cmd_call` 的零行反馈。
- 事实：两个相邻字面量均无插值，Ruff F541 ×2；拼接结果为普通中文提示。
- 引用/注册证据与检索范围：S1/S2；_cmd_call 由 CLI 分发，零行分支实际可达，不能删除提示或 return 5。无动态表达式/副作用。
- 尚未排除调用：已安装副本未读；本项只改仓库模板。
- 风险/置信度/影响面：低；高/高；只是两个前缀。
- 建议：去掉 f，保留完全一致的 stdout/stderr、返回码与落盘行为。
- 验证：离线注入空 records，对比提示文本、stderr 及 return 5；模板 lint。其余 4 个 I001 属 import 排序线索，不能以“无用 import”名义删除，重排前要核对导入副作用。
- 预期收益：去掉两条纯语法告警，没有可测性能收益。

## B 类候选：需要独立方案

### CLN-014：两种轮询 Hook 的共同循环

- 类别、位置：重构重复逻辑；`frontend/src/hooks/useVisibleRefresh.ts:4`、`frontend/src/components/tasks/useTaskPolling.ts:4`。
- 事实、引用/注册证据与检索范围：S1/S2；前者用于 App.tsx:44 的 30 秒任务摘要、useChatStream.ts:1473 的当前任务会话 3 秒增量；后者用于 TaskCenter.tsx:32 的 10 秒列表和 TaskRunsDrawer.tsx:33/65 的 3 秒记录/详情。均有可见性监听、AbortController 与 setTimeout，调用职责不同。

| 比较面 | useVisibleRefresh | useTaskPolling |
|---|---|---|
| 首次请求 | 可见且 enabled 后先等 interval；恢复可见时立即 | 挂载后可见则立即 |
| 启用条件 | 显式 enabled | 挂载/传入 load 决定，无 enabled 参数 |
| 错误 | 静默保留上次状态；refresh 自行写外部状态 | 清掉旧 data，返回错误文本 |
| 返回值 | void | data/error，load 身份变化立即撤旧数据 |
| 状态归属 | 调用方的 epoch/generation/会话校验 | snapshot.load + disposed/signal 校验 |
| 取消/时序 | 隐藏/卸载 abort；下一次等待完成后排期 | 同样取消；poll 起始另 abort 上次 controller |
| 权限/事务 | HTTP 服务端权限不在 Hook 合并 | 列表/记录的错误撤下是安全展示语义，不可改成静默保留 |

- 尚未排除调用：两个 refresh/load 的不响应取消延迟情况未做浏览器实测；取消并不保证任意 Promise 立刻结束。不要为了通用参数化引入更多配置分支。
- 风险/置信度/影响面：中；事实高、值得抽取中；任务管理、聊天同步、侧栏摘要。
- 建议：当前保留两个 Hook。只有未来新增同类需求时，先设计不拥有业务状态的小循环，并显式保留首次请求/错误/取消策略；不能把所有会话正文改为轮询。
- 验证：假定时钟覆盖首次延迟/立即、隐藏恢复、卸载/换用户、忽略 abort 的迟到返回、错误撤旧与静默保留；原有任务增量/侧栏分页/SSE 测试不变。
- 预期收益：可能减少循环维护重复，尚无收益数据；额外抽象若更复杂则放弃。

### CLN-015：启动脚本两个完全相同的就绪等待

- 类别、位置：重构重复逻辑；`scripts/start.sh:132` wait_for、`:145` wait_for_backend。
- 事实、引用/注册证据与检索范围：S1/S2；两个函数体当前相同，都轮询 curl -sf、每秒等待、超时返回 1。调用分别在 173/175 附近，后端实际传 /api/ready，前端传根 URL。脚本由 README、restart.sh 等使用；不应删除后端探测。
- 语义比较：无权限/DB 事务；同样三参数（URL、名称、超时）、默认 30 秒、相同 stdout/返回码、shell 信号与 set -e 语义，调用处用 && 记录就绪；区别只在调用 URL/显示名。
- 尚未排除调用：Linux/macOS 实际启动、curl 挂起与信号行为未验证；不能顺手改变 timeout/curl 选项。
- 风险/置信度/影响面：低，但重复抽取列 B；高/高；本地启动脚本。
- 建议：复用已存在 wait_for，替换后端调用并删重复函数；不新增脚本公共库。
- 验证：bash -n；离线 stub curl/sleep 验证即刻成功、重试成功、超时、frontend-only、后端 /api/ready URL 和返回码，禁止启动真实服务。
- 预期收益：减少一份完全相同实现和漂移点，不估算启动加速。

### CLN-016：大文件按职责拆分

- 类别、位置：重构职责；`frontend/src/hooks/useChatStream.ts:121/649`（1551 行）、`frontend/src/state/session.tsx:100/493`（1289 行）、`src/melonclaw/services/execution.py:58/70/537`（862 行）；次级 resource_service.py 796、attachments.py 857、memory/service.py 730。
- 事实、引用/注册证据与检索范围：S1/S2；流 Hook 负责 reducer/会话缓存/增量批量/SSE/历史恢复，session 负责上下文、列表及请求世代，ExecutionService 负责 prepare/重放/审批/流收尾。入口均在当前功能链路，无“因长可删”的证据。后端 900 行上限由 tests/test_architecture.py 强制。
- 合并/拆分边界：epoch 与 AbortController 不可由新共享对象跨会话复用；prepare 中幂等检查/锁/消息对顺序不变；终态 CAS/业务消息与 Checkpointer 不混合。异常收尾、取消释放和恢复必须一起建边界。普通聊天与后台 task execution 的权限/进度/事务返回不同，不能合成同一执行函数。
- 尚未排除调用：真实 PostgreSQL 组合与浏览器 E2E 本轮未验证，故不是 A。
- 风险/置信度/影响面：中至高；事实高、收益中；聊天、后台、审批和历史。
- 建议：先独立设计“纯 reducer/消息映射”和“副作用编排”之间边界；优先搬纯函数到同层明确职责模块，不加 forwarding 文件。后端现已存在 execution_finalize/models/trace，先复用现有分工，不能为了凑长度新建包。
- 验证：拆分前后 reducer/事件序列和 OpenAPI 等价、跨会话/切身份/历史补页/终态对账/失败恢复；专用数据库隔离 schema 的幂等、锁、消息对测试必需；架构/900 行规则及完整 check/build。
- 预期收益：降低阅读与修改耦合；行数与性能收益未测，不以缩短总行数作为验收。

### CLN-017：requirements 生成结果漂移

- 类别、位置：依赖清单整理；`requirements.txt:1` 生成命令声明，`pyproject.toml:37` 起新增四个直接依赖，`uv.lock` 中 melonclaw 根依赖列表。
- 事实、引用/注册证据与检索范围：S1/S3；uv lock --check 通过，但对锁文件只读导出发现 requirements 缺 pandas/playwright/pycryptodome 及相关 numpy/pyee/python-dateutil/six 等条目，requests 的根依赖注释也未同步。新增依赖提交为 6ac5085，requirements 历史最新为 1a85876。
- 尚未排除调用：pip 兼容安装实际覆盖范围、所有已安装 Skill 对新增包的需求未验证；包移除须先完成 CLN-019。
- 风险/置信度/影响面：中；高/高；pip 安装与 uv 安装的一致性，属于依赖相关 B 项。
- 建议：后续依赖决策稳定后，用文件头的 `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt` 重新生成，不能手工删条目或编辑锁文件。若保留当前依赖，也可单独同步生成文件；不顺手升级任何版本。
- 验证：再次锁文件导出应等价（临时路径/命令注释除外）、uv lock --check；隔离环境做 uv/pip 安装与核心/示例/模板导入核对，不连接数据库或 API。若新增检查门禁，须同步 scripts/check.sh、CI 和 AGENTS 检查章节。
- 预期收益：减少两套安装结果漂移，非“删除依赖”或已测安装提速。

### CLN-018：前端首屏分块与管理组件加载

- 类别、位置：性能优化；`frontend/src/App.tsx:4/12/13/26` 的 AccountManagement/ChatView/ResourceView 静态导入与 TaskCenter lazy；`frontend/src/components/ResourceView.tsx:25`；`frontend/src/components/Markdown.tsx:13` 起可视化 lazy。
- 事实、引用/注册证据与检索范围：S1/S2；App 确实使用上述组件，构建仍有 >500 kB 块；TaskCenter、图表、MathMarkdown/Mermaid/CodeHighlighter 已懒加载，不能再把这些列为未做优化。
- 尚未排除调用：首屏实际网络请求、两个 index 的依赖来源、字体/图表缓存、品牌/权限下页面保留时序未测；没有包体积/首屏/CI 改善预测。
- 风险/置信度/影响面：中；事实高、优化收益中；首屏、导航/表单生命周期。
- 建议：独立构建分析和网络测量后再考虑非首屏管理页/弹窗懒加载，保持常驻聊天/导航状态、错误边界与加载反馈；仅调 manualChunks 或告警阈值不算减负。
- 验证：固定环境/品牌/缓存条件比较 initial 请求字节与交互耗时，校验管理员/普通用户导航、表单关闭/重开与聊天活流保留；现有检查和 build。
- 预期收益：待实测；当前只能确认分块警告，不能断言删掉某库能改善首屏。

## C 类候选：暂不执行删除

### CLN-019：未完整证明无用的直接依赖

- 类别、位置：依赖裁剪待确认；`pyproject.toml:37` requests、`:38` pandas、`:39` pycryptodome、`:40` playwright；并核对 fastmcp/langsmith/greenlet/python-multipart。
- 事实、引用/注册证据与检索范围：S1/S2/S3 及 uv.lock 依赖图。requests/Crypto/playwright 未在跟踪 Python 源码发现 import，但新增依赖与外部 Skill/Shell 使用可能相关；requests 同时被 Tavily/LangSmith/google-genai/tiktoken 等依赖，移除直接声明通常不会移除安装包。pandas 在内置 tsfetch.py:387/398 的 Parquet/DataFrame 分支使用，SKILL.md 明确列为 optional。fastmcp 被两个 MCP 示例和测试服务使用；langsmith 有框架 tracing 依赖；greenlet 被 SQLAlchemy 异步路径依赖；python-multipart 是 FastAPI 表单/上传运行需要，不靠 import 数证明。
- 尚未排除调用：外部已安装 Skill/Shell 生成脚本、stdio MCP 配置命令、追踪运行配置及部署环境均未验证。用户运行数据排除使 pycryptodome/playwright 的删除证据不足。
- 风险/置信度/影响面：中至高；事实高，删除置信度低；模型、工具、上传、示例和用户 Skill。
- 建议：先获知所支持 Skill/脚本的依赖契约，决定应用必需与可选分组；目前不删除任何包。pandas 有明确消费者，不能列为死依赖；示例可独立运行是保留 fastmcp 的有效用途。
- 验证：用隔离环境分别覆盖应用、示例 MCP、模板 CSV/JSON/Parquet/DataFrame、外部 Skill 的离线导入/浏览器能力；需要真实 API 的部分另标未验证。uv 管理版本与生成 requirements，不手工裁锁。
- 预期收益：待确认；不虚构下载/磁盘/镜像体积减幅。

### CLN-020：没有当前派发证据的 session action

- 类别、位置：删除死代码候选；`frontend/src/state/session.tsx:100` 的 SessionAction；case `conversationsCleared:261`、`modelsCleared:277`、`userSwitched:338`、`runsCleared:419`。
- 事实、引用/注册证据与检索范围：S1/S2/S3；每个字符串当前仅出现在类型与 reducer case，未找到派发（含测试）。77e4495 明确移除 userSwitched/runsCleared 的旧派发；当前 changeUser:749 调 switchUser，AuthGate 重建 Provider 清上下文。另两个 action 的退出原因未完整追溯。
- 尚未排除调用：组合对象/动态 action、认证跨标签页和未加载页面状态的完整实际行为未运行验证；这些分支涉及身份状态清理，不能凭两个出现次数列 A。
- 风险/置信度/影响面：高；无直接派发事实高、删除中；用户切换、模型目录、会话列表与活流状态。
- 建议：暂保留；先写当前 AuthGate/provider 重建、401/409、失败切换、storage 事件与迟到返回的状态转移表。若证明替代清理完整，下一轮只删对应联合成员/case，不删当前 cleanup/epoch/abort。
- 验证：原 session/auth/跨会话测试保留，补缺失行为覆盖并确认正常/失败/重试/恢复均落到当前路径，再 full check/build。
- 预期收益：可能减少过时 reducer 状态路径；不是授权重写身份模型。

### CLN-021：公开方法“无仓库调用”的证据不足

- 类别、位置：删除死代码候选；`src/melonclaw/memory/service.py:66` MemoryRecord.as_dict、`:696` MemoryService.publish_curated；`repository/attachments.py:198` get_attachment；`repository/resources.py:106` get_skill_user_state；`services/skills.py:139` SkillCatalog.public_items；`core/config.py:163` Settings.psycopg_database_url。
- 事实、引用/注册证据与检索范围：S1/S2；当前精确符号检索未发现调用（property 除定义外未见使用）。MemoryRecord/MemoryService 经 memory.__all__ 导出，publish_curated 注释明确为受控后台/API 的发布入口；repository 的方法通过 BusinessRepository mixin 暴露。Settings 属性和 SkillCatalog 同样是公开成员，无本轮框架 hook 证据，但公开导出不能当私有死函数处理。
- 语义比较：get_attachment 返回展示 dict，而相邻 get_attachment_record 返回原始行；for_user 版本另带身份/状态/作用域限制。publish_curated 的 operation/source/授权与 Agent proposal 不同，不能改为后者的简单别名。psycopg 连接推导底层仍被 Checkpointer/Store 使用。
- 尚未排除调用：仓库外管理脚本/外部 Skill、受控手动 Memory 发布、外部 Python 消费者和恢复运维调用未验证。
- 风险/置信度/影响面：中至高；未见仓库调用事实高、可删除低；发布权限、数据序列化、资源查询和配置接口。
- 建议：先明确公开 Python 接口契约与外部脚本使用，逐符号决定；当前保留，不机械合并 raw/public/for_user 查询，不删底层连接函数。
- 验证：权限与脱敏快照、Memory 幂等/发布、附件原始/展示差异、Skill 目录、Checkpoint/Store 初始化专项；数据库仅专用连接+隔离 schema，外部消费者需单独确认。
- 预期收益：潜在缩小公开维护面，尚不能确认有可删除功能。

### CLN-022：当前不再产生的文件/Shell 审批展示分支

- 类别、位置：删除死代码候选；`frontend/src/components/ApprovalActionSummary.tsx:15/27` 的 execute/write_file/edit_file/delete 展示；`ApprovalPanel.tsx:26/58` 的 edit/respond 决策 UI；后端 `core/hitl.py:259` 附近 `_normalize_decisions` 的对应解析。
- 事实、引用/注册证据与检索范围：S1/S2/S3；当前固定清单仅三种安装/测试工具且 approve/reject，文件与 MCP 调用已免审批。展示组件仍被 ApprovalPanel 使用，解析仍从 build_resume_command 调用，属于活的通用分发而非孤立模块。
- 尚未排除调用：已有 Checkpoint/业务审批快照仍可能含旧文件/Shell action 或 edit/respond allowed_decisions；本轮禁止读/清历史，无法证明这些恢复消费者不存在。未知审批动作的通用反馈也不能连带删除。
- 风险/置信度/影响面：高；当前新请求不产生这些 action 的事实高、可删除低；已有审批恢复、权限和用户可见对象。
- 建议：保留；本轮只将文档说明修正为当前新请求规则（CLN-011），不删除历史恢复/展示，不增加兼容分支或清理旧消息。
- 验证：用合成旧 pending snapshot 覆盖展示/allowed_decisions/批次绑定/拒绝/恢复，并在明确现存数据策略后独立决定。当前审批、安装和 user-input 测试不能删除。
- 预期收益：仅可能减少旧展示分支；当前证据不足，不纳入首批收益。

### CLN-023：没有仓库引用的旧 README 品牌图

- 类别、位置：删除死资源候选；`frontend/public/assets/brand/melonclaw-readme.png`，二进制资源，无符号/起始行（不适用）。
- 事实、引用/注册证据与检索范围：S1/S2/S3；当前 README、docs、源码/样式、品牌构建映射和内置 Skill 未发现文件名引用。历史 README 曾引用该资源，当前 README 已无此图。当前 favicon/mark/word/login 图和 skill-tool 有独立真实用途，不能连带删。
- 尚未排除调用：public 目录按静态 URL 发布，仓库外文档、收藏链接或远端引用可能直接请求该路径；没有生产访问日志，无法证明无外部消费者。品牌模板构造虽未见选择 readme 的路径，也不能排除外部静态引用。
- 风险/置信度/影响面：中；无仓库引用事实高、删除低；已发布静态资源 URL。
- 建议：暂保留；先核实对外资源 URL 是否属于需保留接口及外部引用，再单独决定是否删除，不用当前 UI 未渲染作为理由。
- 验证：公开资源契约/外部引用核实、两种品牌构建资源清单；具备日志授权时再看访问证据。当前仅可验证无仓库显式引用，运行消费者未验证。
- 预期收益：可能减少一项分发资源，未统计磁盘/传输收益；不计为首屏减包收益。

## 用户提供计划的补充复核（2026-10-17）

来源为用户本轮提供的另一份 AI 清理计划。它是审计线索，不是执行结果或仓库事实依据。本节逐项复读当前实现、测试、注册和必要历史；沿用原 CLN-001～023，新增 CLN-024～038，避免两份计划的同号条目混淆。复核开始和结束时 HEAD 均仍为上述提交，工作区只有本报告未跟踪；本次仅更新本报告。原检查基线沿用本报告实际执行记录，不把对方报告的运行耗时和验证声明计为本次实测。

补充检索 S4：在 S1/S2 范围内使用 `git grep` 检查跟踪的源码、测试、scripts、ops、example、docs、内置 Skill 和 `.agents` 中符号/模块名；另用 `rg` 复核当前前端源码和测试的 import、mock、JSX、状态读写、动态加载及资源字符串。逐项阅读下列函数体及调用者，仍排除依赖目录、构建缓存、敏感配置和运行数据。仓库外 Python 导入、运行时安装 Skill、生产资源请求与浏览器完整时序仍未验证。

补充交付验证：`uv run pytest -q tests/test_docs_links.py` 为 2 passed；`git diff --check`、`git diff --exit-code` 通过，跟踪文件无改动，状态仍仅本报告未跟踪。本次只补充报告，未重复运行完整检查/前端构建，上文基线仍是初次审计实测；各新增候选的“验证”均为下一轮实施验收要求，不冒充本次已执行。

### 采纳与调整对应表

| 对方计划 | 本计划处理 |
|---|---|
| 001、002 的 `_call_key`、003 的三个方法、004 的 `_skill_dir`、006、008、010、011 的既有文档/注释 | 已在 CLN-001～013 覆盖，不重复计项；新增事实见 024、035。 |
| 002 的投影别名、004 的公开常量；005；007；009 | 分别进入 027、024、026、025；不把公开导出或协议/权限参数直接定为 A。 |
| 012、013、014、015 | 分成 028～034，区分测试迁移、状态、时钟、事务、配置和权限差异。 |
| 016～021、022、023 的既有公开方法 | 分别已由 015、014、016、017/018/019、020/021 覆盖；入口约束补充 036；状态机测试护栏明确保留。 |
| 024 | 旧审批和品牌图沿用 022/023；合规组件、图标另列 037/038。 |

以下内容不照搬：

- `repository/tasks.py` 当前没有 `list_scope`。真实实现是 `list_tasks:353` 内 `system_admin` exists 条件与 `scope(tasks, user_id)` 的 OR；接受“列表概览与操作权限不可合并”的结论，拒绝错误符号和行号。
- `services/conversations.py:39` 没有 `USER_ID_RE`；当前 `resolve_user:45` 做去空白、非空和数据库有效用户/租户查询。CLN-024 删除闲置正则的建议，不以“同一正则已下沉”为理由，更不补回旧校验。
- `load_mcp_tools` 不只是返回列表的等价包装：还有空配置返回、取消传播及异常脱敏重抛。不能声称现有其他测试已完整覆盖它的契约。
- 显式环境文件和默认 `.env` 的两次加载可能补入不同配置，不能无条件认定等价重复；见 CLN-033。
- 旧供应商计划仍写“进行中”，虽步骤全勾选，历史验收和决策存在过时/冲突内容；不能仅据勾选就宣称当前全部验收完成。
- 不为拆文件保留额外兼容性 re-export；若有真实调用则同步迁移调用者。也不采用覆盖整个路径的 `git checkout -- <paths>` 回退，以免覆盖用户改动。

### CLN-024：未使用常量与关联 import（A）

- 类别、位置：删除死代码；`src/melonclaw/services/chat.py:58` USER_ID_RE 及 `:6` re；`output/assistant_steps.py:17` STEP_STATUSES；`services/skills.py:18` MAX_SKILL_NAME_LENGTH、`:23` SKILL_SCOPES。
- 事实与证据：S4 中这些常量只有定义；chat 的 re 仅服务该定义。均未列入所在模块的显式 `__all__`，未见框架/工具注册、字符串查询或导入副作用职责。STEP_STATUSES 不参与实际状态归约，TOOL_STATUSES 有消费者须保留；read_skill:93 目前直接执行长度 64 校验。
- 尚未排除调用：仓库外按模块直接导入未验证；仓库内脚本、Skill、测试和动态注册未找到消费者。结论限当前应用实现。
- 风险/置信度/影响面：低；高/高；只消除静态常量初始化和关联 import，不改变用户校验、流状态或 Skill 输入规则。
- 建议：删除上述闲置声明及 chat 的 re import。选择保留现有字面量 64，不把“改用常量”混进删除死代码项；后者可另作重构，勿改变上限。
- 验证：检查删除后精确符号/import 无悬空引用；运行现有聊天、assistant_steps、Skill 解析测试及完整 check.sh；长度边界、错误消息和 TOOL_STATUSES 保持原样。
- 预期收益：减少误导性状态集合、正则和 Skill 配置声明；不量化性能收益。

### CLN-025：已退出运行链的状态 Hook（A）

- 类别、位置：删除死代码；`frontend/src/hooks/useServiceStatus.ts:5`（整文件）、`state/session.tsx:437` deriveRunStatus、`frontend/tests/sidebar-actions.test.tsx:43` mock。
- 事实与证据：S4 中 deriveRunStatus 只被该 Hook 调用，Hook 唯一外部出现是测试 mock；Sidebar 不导入它，GlobalNav:23 从 useSession 读取服务状态。模块顶层只有 import 和函数声明，无注册/订阅副作用；未见 glob 自动加载。mock 的工厂本身也不导入真实 Hook。
- 尚未排除调用：未进行浏览器 E2E，但仓库组件、回调、导出消费和测试调用链已核对；不存在以该名称注册的运行入口。
- 风险/置信度/影响面：低；高/高；旧便利 Hook、旧 pill 推导函数与一行失效 mock；不调整 SessionState、setRunStatus 或实际服务状态刷新。
- 建议：三处一起清理；保留 sidebar-actions 的所有测试用例和断言。全局只写状态另列 CLN-030，不能随本项删除。
- 验证：前端 lint/typecheck/test/build；侧栏操作与 GlobalNav 状态展示/刷新既有测试通过。删除 mock 不应减少测试数、跳过数或行为断言。
- 预期收益：消除一条完整的孤立调用链，避免把测试 mock 误认为生产消费者。

### CLN-026：未读取参数，但调用边界需保护（C）

- 类别、位置：删除死代码候选；`src/melonclaw/output/events.py:378` _consume_subagents.parent_scope；`tool/tools.py:59` build_custom_tools.settings；`services/resource_service.py:471` _require_provider_row.user_id。
- 事实与证据：S4 和函数体确认三参数不读；调用分别在 events:352/449、tools:212、resource_service:497/529。子 Agent scope 由 handle.path/name 和 _parent_call_id 构造；工具工厂返回 internet_search；权限 helper 仍先 _require_admin(role) 再取 provider，调用者仍先 await _role(user_id)。
- 尚未排除调用：公开 build_custom_tools 的仓库外调用；子 Agent 递归/异常/取消与权限异步时序的动态回归未在补充轮执行。不能把本项扩展到相似的协议必需参数。
- 风险/置信度/影响面：中–高；事实高/实施中；流协议、工具装配和供应商权限入口，因此按本次标准列 C，而非对方 A。
- 建议：只在独立补证后分别移除形参和实参；保留参数求值以外所有 await 顺序、异常、取消传播和返回值，尤其不能删除 _role/_require_admin 或 build_agent_tools 的 settings 签名。
- 验证：parallel_tool_stream、agent_tool_assembly、mcp_tools、model_configs 等专项；补递归失败/取消和非管理员拒绝矩阵，无真实 MCP/模型调用。逐个子项通过后再升级可执行等级。
- 预期收益：让签名准确反映依赖；没有已测性能收益。

### CLN-027：没有仓库消费者的显式导出（C）

- 类别、位置：删除死代码候选；`src/melonclaw/output/assistant_steps.py:630/632` AssistantStepProjector 别名及 __all__；`services/resource_service.py:36/785` MCP_TRANSPORTS 及 __all__。
- 事实与证据：S4 仅找到声明与导出，实际投影使用 AssistantStepAccumulator，MCP 管理使用 mcp_management.py:16 TRANSPORTS；没有发现以旧名字进行的框架注册。
- 尚未排除调用：显式公开导出可能被仓库外脚本或安装 Skill 按名/星号导入，未验证；不能用“独立应用”推定没有接口契约。
- 风险/置信度/影响面：中；事实高/可删除低；Python 模块公开表面。
- 建议：先核实外部使用契约，再同时删定义与 __all__ 项；保留当前 accumulator 和管理校验，勿新增兼容层。
- 验证：外部消费者清单确认；流式投影与 MCP 管理测试、完整 check.sh；现有 import 集合和动态工具目录保持一致。
- 预期收益：减少旧名称和重复声明；当前不授权删除。

### CLN-028：仅单测使用的前端摘要/解析包装（B）

- 类别、位置：删除死代码候选；`frontend/src/lib/agentRun.ts:123/134` AgentToolSummary / summariseAgentTools；`lib/resultBlocks.ts:119` parseResultBlock。
- 事实与证据：S4 业务侧无调用；前者被 agent-execution.test.tsx:341/351 测试，后者被 result-components.test.tsx:24–26/59 测试。真实渲染解析使用 parseResultBlockDetailed；parseResultBlock 仅返回其 .result，无额外 catch 或副作用。摘要函数包含计数/去重/最多三项/unknown 归失败逻辑，不是简单转发。
- 尚未排除调用：需逐条确认旧摘要产品职责已退场，而非断言与新 UI 恰巧同名；不能声称 runActivity 已等价覆盖全部旧计数规则。
- 风险/置信度/影响面：中；事实高/实施中；两个导出、关联类型/import 与现有测试。
- 建议：先迁移 parseResultBlock 的全部有效/非法/超长输入与 sources 引号测试到 Detailed.result，再删包装。摘要测试只在证明“摘要条目统计”行为已删除后才移除；保留 AgentExecution 工具状态和 FailureNotice/run-activity 当前展示覆盖。toolSummary import 仅该函数使用，删除函数后同步核对 import。
- 验证：列出逐条旧断言→新断言/已退出行为映射；保持 URL/路径拒绝、格式验证和超长输入断言；完整前端检查及 build。测试通过不是调用不存在的证明。
- 预期收益：减少由旧单测维持的 API；不以减少测试数为收益。

### CLN-029：公开 MCP loader 不能视为等价空壳（C）

- 类别、位置：删除死代码候选；`src/melonclaw/tool/tools.py:176` load_mcp_tools；`tests/test_mcp_tools.py:55` 对应公开 loader 测试。
- 事实与证据：S4 仅此测试调用，build_agent_tools:198 直接调 _load_mcp_tools_with_catalog。公开函数包含空 servers 返回、CancelledError 重抛及其他异常脱敏 RuntimeError；生产装配还添加目录/失败信息工具，返回内容与错误语义不同。
- 尚未排除调用：外部脚本/Skill 的公开导入，以及 wrapper 独有异常契约；现有健康/失败混合服务器测试不足以证明所有路径覆盖。
- 风险/置信度/影响面：高；事实高/可删除低；MCP 公开装配接口与测试。
- 建议：先确认公开契约退出，再证明/迁移部分发现失败、全失败、空配置、白名单、取消与异常脱敏覆盖；不机械删 test_public_loader 用例，不把 build_agent_tools 当成无差异替代。
- 验证：离线 FakeTool/Client 测试与调用契约审查、完整 check.sh；不连接真实 MCP。
- 预期收益：可能减少一套错误包装，证据不足前保留。

### CLN-030：旧 pill 链路退出后的全局 runStatus（C）

- 类别、位置：删除死状态候选；`frontend/src/state/session.tsx:46/95/150/425/480/1088` 类型、字段、action、reducer、setter；`hooks/useChatStream.ts:807` 起多处 setRunStatus。
- 事实与证据：S4 显示除 CLN-025 链路外无业务读取者；但 session:559/990 还有直接 dispatch 重置，不能只删 useChatStream 的 19 个 setter 调用。chat-stream.test.tsx 明确断言 waiting/failed/starting/responding/null。
- 尚未排除调用：删除 dispatch 会减少 Context 重渲染，是否存在隐式时序依赖未验证；认证切换、恢复、任务同步均触及这里。其他 AgentRunStatus/runStatusFromMessage 是活跃消息状态，不属于此项。
- 风险/置信度/影响面：高；事实高/实施中；全局状态、SSE 生命周期与测试。因此从对方 B 调为 C。
- 建议：CLN-025 后单独设计；穷尽直接 dispatch、setter、memo 依赖和测试夹具，迁移断言到实际消息状态/phase；保持 epoch、AbortController、去重、终态对账及三种刷新职责。
- 验证：普通/后台聊天、失败、取消、重试、恢复、401/409/切换身份、跨会话与隐藏页测试及浏览器时序检查，完整前端检查和 build。
- 预期收益：减少无消费者状态维护；减少渲染的实际效果未测，不报性能数字。

### CLN-031：重复 UTC 时钟 helper（B）

- 类别、位置：重构重复逻辑；`src/melonclaw/repository/tasks.py:32` now 与 `repository/mappers.py:15` _now。
- 事实与证据：S4 和函数体均为 datetime.now(UTC)；tasks 的调用用于入队、取消和更新时间，未发现仓库测试对 tasks.now 的补丁。两函数无 I/O、无 await、无权限判断，返回 UTC datetime。
- 尚未排除调用：外部导入/补丁未验证；把多次调用顺手缓存成一次会改变时间语义，不属于此建议。
- 风险/置信度/影响面：中；事实高/实施中；任务仓储的时间来源，现有事务/锁边界须原样保留。
- 建议：在原调用位置复用 _now，不移动求值时机、不合并 timestamp、不扩大共享抽象；检查 datetime/UTC 是否还有其他使用再清 import。
- 验证：任务入队、运行取消、幂等/恢复测试；涉及 DB 的验收仅专用连接隔离 schema。没有连接时此部分仍未验证。
- 预期收益：统一同层现有工具函数，减少一份重复实现；不宣称性能改善。

### CLN-032：测试 helper 与生产账本判断重复（C）

- 类别、位置：重构重复逻辑；`src/melonclaw/repository/user_interactions.py:35` _is_open_ledger 与 `:105` create_or_get_user_interaction 的内联条件。
- 事实与证据：S4 和源码确认同为 row 非 None 且 str(status)==WAITING；helper 仅由 tests/test_user_input.py:773 起测试。生产判断发生在 engine.begin 内的读取之后，随后可能归档旧 interrupt_id 并插入新账本。
- 尚未排除调用：纯条件等价已核对，但真实并发/重试事务行为未动态验证；单测 helper 不等于测试实际事务路径。
- 风险/置信度/影响面：高；事实高/实施中；HITL 交互账本事务，因此不沿用对方“低风险”结论。
- 建议：如实施，只把原条件替为 _is_open_ledger(existing)，保留读写顺序、唯一键、归档与异常策略；不借机重构状态机或锁。
- 验证：保留 helper 状态矩阵，并用隔离 DB 验证 waiting 复用、closed 归档、相同请求重试/冲突和事务失败回滚。无专用连接不宣称已通过。
- 预期收益：让被单测验证的条件真正用于生产分支，避免双份判断漂移。

### CLN-033：重复环境加载可能改变配置来源（C）

- 类别、位置：重构重复逻辑候选；`src/melonclaw/main_password.py:5/24` load_dotenv；`src/melonclaw/__init__.py:8` 起环境加载分支。
- 事实与证据：包导入先按显式 MELONCLAW_ENV_FILE、系统文件、默认文件择一加载；main 又无参加载默认文件。这不是同一输入可保证的重复。main 由 pyproject 命令入口调用，随后连接数据库并重置密码，不能实际运行验证。
- 尚未排除调用：未读取真实运行配置；显式文件缺失某键时，第二次加载可能补入默认文件值，从而改变数据库/配置选择（推断，未实测）。
- 风险/置信度/影响面：高；事实高/等价删除低；管理 CLI 的配置来源，存在导入副作用。
- 建议：保留现状到配置契约明确；若要统一加载，独立方案说明默认文件补值是否退出，不作为纯清理直接删除。
- 验证：用临时无凭据环境文件、隔离子进程和 stub 数据库验证加载顺序/缺失键/既有环境优先级；禁止调用真实密码重置入口。
- 预期收益：可能消除隐式配置混用，未证明行为等价前不执行。

### CLN-034：聊天 MCP/Skill 安装身份校验（C）

- 类别、位置：重构重复逻辑候选；`src/melonclaw/services/mcp_install.py:16` 与 `services/skill_install.py:19` _identity。
- 事实与证据：S4 读到相同顺序的上下文、用户租户、ready、会话、项目归属和项目可见性核验。异常分别 McpConfigError / SkillImportError，部分文案不同；返回分别为 storage/user_id/上下文 conversation_id 与 user_id/数据库 conversation id/project_id。MCP 经 _draft:33 间接调用，不仅是 prepare/test/confirm 直接调用。
- 尚未排除调用：每次安装/测试/确认时重新校验的时序与数据库变化窗口需保留；共享结果缓存可能绕过二次权限检查。外层事务、草稿幂等、异常映射也不相同。
- 风险/置信度/影响面：高；事实高/直接合并低；权限与审批边界，按本轮规则列 C。
- 建议：目前保留两份明确核验；只有证明共同职责且维护收益成立时才独立抽取，保持具体异常、文案、返回值与每次核验时机，勿引入公共继承/缓存/额外转发层。
- 验证：chat_mcp_install、chat_skill_install、chat_skill_creation 及跨租户/用户/项目变更、过期草稿、拒绝与重试矩阵；真实 DB 部分用隔离测试环境。
- 预期收益：可能降低核验漏改风险；代码行数下降本身不是合并理由。

### CLN-035：旧供应商计划应标明历史状态（B）

- 类别、位置：文档整理；`docs/exec-plans/active/2026-09-26-model-provider-yuxi-parity.md:3/17/29/38` 状态、步骤、验收及决策；执行计划 README:23 的归档约定。
- 事实与证据：步骤全部勾选，标题状态仍为进行中；文内仍有默认模型种子、环境模型凭据、清库及不同权限方案的历史描述，与当前 AGENTS/模型配置规则不一致。S4 未找到其他跟踪文档按此文件名链接；这不能证明旧实机验收曾完成。
- 尚未排除事项：历史外部验收未验证，不能重跑旧 db-init/清库命令来确认完成；note 草稿不作事实依据。
- 风险/置信度/影响面：低；事实高/全部完成认定中；只影响文档状态和路径。
- 建议：逐条区分已实现与已被后续方案替代，写明历史边界和当前权威文档后按约定归档；不追改历史决策成当前事实，不宣称未验证步骤通过。
- 验证：对照当前模型 schema/种子/服务和测试记录，修正全部路径引用，通过 docs_links；无数据库操作。
- 预期收益：active 目录只保留真实待办，避免旧清库/种子说明误导执行。

### CLN-036：API 导入例外与 CLI 清单不是同一集合（B）

- 类别、位置：重构重复配置；`tests/test_architecture.py:81/140` ENTRY_FILES 及 API 导入测试；`docs/ARCHITECTURE.md:91/93` 入口行与补充规则；`pyproject.toml:43` 起命令表。
- 事实与证据：注册五个 CLI，但 ENTRY_FILES 只放 main_web/main_db_init；其用途是允许导入 api，不是注册命令。main_db_init 当前不导入 api；架构正文却声明 api 只准 main_web 导入。
- 尚未排除事项：应先确认文档的独占规则就是预期架构；不能为“补全五入口”而扩大 API 导入许可。
- 风险/置信度/影响面：中；事实高/实施中；架构测试的规则表、命名与文档。
- 建议：接受对方详细方案的方向：若独占规则确认，将例外明确命名为 API_ENTRY_FILES 并只保留 main_web；另在文档完整列五个 CLI，避免混淆职责。不改变 CLI 注册和运行行为。
- 验证：架构测试、AST 导入扫描与文档链接；确认规则仍阻止非 Web 入口反向导入 api。无需执行任何 CLI。
- 预期收益：消除文档/机械门禁不一致，减少不必要的导入例外。

### CLN-037：合规声明挂载已删除，产品意图待确认（C）

- 类别、位置：删除死代码候选；`frontend/src/components/ComplianceNotice.tsx:27` ComplianceStatement、`styles/compliance.css:1` 起专用样式；`frontend/tests/ComplianceNotice.test.tsx:39`；`docs/design-docs/compliance-notice.md:9`。
- 事实与证据：S4 仅测试渲染该组件；`git show 1d9b4df -- frontend/src/components/ChatView.tsx` 明确删除 import/JSX，文档仍说首页显示。相同模块的 AccessNoticeGate:8 及样式 import 仍活跃，不能整文件删除。
- 尚未排除事项：移除挂载是否代表正式取消产品要求未验证；历史提交不能单独证明预期去留。本项不评价声明的法律充分性。
- 风险/置信度/影响面：中–高；事实高/可删除低；产品声明、对应样式/测试/文档。
- 建议：先确认是否已正式取消页脚要求，再决定清理旧组件与专用选择器或另做挂载修复；本轮不恢复展示、不删除测试。保留 AccessNoticeGate 和访问确认存储行为。
- 验证：确认产品意图后核对 RMS/默认品牌、访问确认、首页及声明测试的覆盖去向；完整前端检查/build。
- 预期收益：消除实现与文档冲突，去留确认前不计删除收益。

### CLN-038：基础图标枚举中的低使用资源（C）

- 类别、位置：删除死资源候选；`frontend/src/components/Icon.tsx:21/36` external-link、message-square-plus；对应 `frontend/public/assets/icons/` SVG（资源无代码起始行）。
- 事实与证据：S4 当前源码只在 ICON_NAMES 中出现两名称；Icon:71 按 name 拼接资源路径，IconName 也由数组派生，因此数组本身是类型接口，不是零引用集合。
- 尚未排除调用：外部静态 URL、构建品牌环境、动态名称来源和仓库外消费者未验证；对方所称图标测试枚举覆盖未作为已验证事实采纳。
- 风险/置信度/影响面：中；事实高/可删除低；图标类型与公开静态文件。
- 建议：暂保留；先完成动态 IconName 来源与资源使用清单、外部访问证据，再考虑同时清理枚举/资源。不要影响其他品牌映射与许可证。
- 验证：类型检查、两品牌构建、导航/消息 UI 及资源路径验证；不据无文本匹配直接删 SVG。
- 预期收益：可能减少少量资源维护；未测体积收益。

补充保留清单：`repository/user_interaction_lifecycle.py:33/64` 的 ALL_STATUSES / can_transition 被 `tests/test_user_input.py:1126/1136/1156` 等用于状态集合与迁移规则一致性验证，是有效测试护栏；不能仅因没有生产调用删除。`backend/task_readonly.py:46/49` upload_files/download_files 是拒绝协议实现；`services/task_history.py:112` hydrate 的 message_id 与 TaskChatService:20 共用 middleware/task_context.py:28 调用签名，不是可删闲参。前端 `melonclaw-check-draft`（Composer/UserPicker）、`melonclaw:open-sidebar`（ChatView/Sidebar）及认证事件的 dispatch/listener 是真实字符串调用链，须成对保留。

## 容易误删但应保留的入口与边界

| 入口/对象 | 当前使用证据与保留理由 |
|---|---|
| api/routes 下只有定义的路由函数 | api/app.py 导入 router 并 include_router，装饰器完成注册；没有 Python 直接函数调用不代表无 HTTP 消费者。exception_handler 嵌套函数同理。 |
| require_session、lifespan 与 initialize | 全局 Depends + FastAPI lifespan + asyncio.create_task；关闭顺序要先收尾任务/流再关资源。依赖 scope 和账户锁不是冗余包装。 |
| 五个 main_* 命令 | pyproject.toml:43 的 project.scripts 注册；main_web 导入 api.app.main，main_resources 调 storage/deployment；db-init/update 不在请求链仍是必要入口。 |
| database/updates 三个 upgrade、SchemaUpdate/UPDATES | update_runner 按登记 ID 排序调用，在同事务记录；20261015 的 DROP 通知结构对保留历史升级必需，不是应该从代码再删除的兼容分支。 |
| TaskScheduler.tick/execute、jobs.discard | APScheduler add_job(self.tick) 与 asyncio task/done callback 间接调用；queue 会话锁/PID、失效停止和 shutdown/restart 收敛不可删。 |
| configure_task/read_task_history/complete_task | core/agent.py、core/task_agent.py 注入 provider，StructuredTool/@tool 动态运行；权限分别限定普通主 Agent/本轮绑定历史/后台主 Agent，不能互相推广。 |
| middleware 钩子与模型 callback | ToolPoolMiddleware.aafter_model、ModelUsageCallback.on_llm_error 虽单次定义，由框架调用；删除会破坏工具池更新和失败用量事件。 |
| Pydantic validator 与 Process target | validate_schedule/concrete_model/validate_action 由框架触发；parsers/worker.py:54 以 target=_parse_worker 进入 spawn，不能按直接调用数删除。 |
| 主/子/后台工具组装差异 | 普通聊天可文件/受控安装/MCP，子 Agent 无 configure_task，后台无 Shell/MCP/Memory/安装。task_search 不是普通搜索重复：严格 schema、结果上限、异步/关闭连接、禁 raw content 和安全异常与普通搜索不同。 |
| MCP 动态工具与发现协调器 | MultiServerMCPClient.get_tools、工具白名单交集、连接指纹名字、scope/版本检查、取消和共享发现缓存；配置未启用不构成死实现。 |
| 普通聊天 SSE / 任务消息同步 / 侧栏摘要 | 前者按帧 ID/步骤/终态快照；当前任务会话按 seq + pending ID 更新；侧栏只 GET conversations?task_only=true 的单页 100 摘要。三者必须分开，不引入所有会话正文轮询。 |
| epoch、AbortController、chatCache/activeRuns、文本冲刷 | 防迟到响应、身份越界/跨会话污染、切换后续播和 completed 对账；表面重复的 guards 可能作用在不同异步阶段。 |
| 任务列表 system/admin 概览与详情 scope | 最新权限功能故意不同；不得抽成一个“统一任务可见性”来扩大编辑/读取他人会话权限。 |
| 任务消息与 Checkpointer | task_delivery 复用事务写业务消息，后台独立 task-run Checkpoint；普通聊天不收尾后台占位。回执只清 response，永久保留键/摘要拒重。 |
| 文件/结果读取的两种 API | files 任意受控工作区目录与 result-files 的 /outputs/ 协议有不同范围；当前已复用 file_content_response/html_preview_response，不能删除一个 API 或合并到更宽权限。 |
| ResultSources/ResultRenderBoundary/ResultBlockView | ResultBlockView 按类型分派 sources/chart，图表懒加载并带边界；只扫组件 JSX 名或未选该结果类型会误判。 |
| Icon/provider/brand 资源 | Icon.tsx:72 按 name 拼 SVG 路径；providerIcons.ts 的 avatar 拼路径；Vite transformIndexHtml 选择品牌 favicon；选中导航另用 alarm-clock-filled/blocks-filled，SkillLogo 使用 skill-tool。旧 README 图另列 CLN-023，不据此整批删除品牌图。许可证和 SOURCE.json 应保留来源信息。 |
| 动态 CSS 类名 | status、source、task/执行节点、主题变量和选中态按字符串拼接；没有浏览器覆盖和所有状态枚举，未确认任何整块 CSS 可删。 |
| 内置 Skill 与示例 | tsdoc/tsfetch/template/reference 从 SKILL.md 的相对命令进入；skill-creator/tutorial 通过资源复制、索引及 Agent Skill 装载进入。example/chat 有 CLI __main__ 与 README，MCP server 有脚本入口。不是业务 import 图中的孤立垃圾。 |
| tests/account_fixtures.py、task_process_fixture.py | 测试 import/子进程入口，不以 test_ 命名不是无用；review 回归与两个 strict xfail 记录实际缺口，不能因旧/失败删除。 |

公共重复的异常构造函数（repository/errors.py 的三种异常）虽 AST 函数体相同，异常类型、HTTP 分类和调用捕获不同，收益不足，不建议增加公共基类/转发层。ChatService 的其他公开门面是隔离 HTTP 与内部服务的边界，不纳入 CLN-003。

## 已经清理、不必重复处理

1. **旧 v2/legacy 事件适配器**已在 d8b1587 移除，当前只走 v3；剩余 `_call_key` 单独列 CLN-002，不重新建立兼容流。
2. **任务专用回复、报告引用草稿/API/字段、全局任务通知 UI/API/已读字段**已退出当前实现。只保留升级所需旧字段名字，不再清理正文或重建任务表。
3. **TaskDetail 独立详情页**已改 TaskRunsDrawer，presentation/schedule 共用，任务名称直接到固定会话；不再做第二次详情页删除或把完整结果搬回抽屉。
4. **未发布旧任务结构迁移和重复阶段计划**已按现有设计收敛；已登记升级不能跟着删。当前验收测试继续覆盖取消/重启/幂等/消息事务。
5. **文件工具逐次审批与动态 MCP 逐次审批登记**已在 48200b3/c76712d 调整；旧 execute 注释配置本体已删除，TODO 所述非沙箱限制仍有效。
6. **任务进度重复落库、状态读取大锁、摘要无界**已有可靠性处理：只在正文变化保存，execution_status 保留 mutex/PID 但不取 task_exclusive 锁，历史摘要有时限/预算/UTF-8 块上限。不当作待实现优化。
7. **可视化/任务页面懒加载、文件响应复用、列表去重/排序**已有实现；性能分析应从现有结果开始，不能把已有 lazy/去重成果重复计算为收益。
8. 常规 ruff 与 TS noUnused 检查均通过；补充复核发现 chat.py 的 re 仅维持闲置常量，可随 CLN-024 删除，说明 lint 通过不能证明声明有消费者。未证明存在确定不可达业务分支。示例/模板排序告警和 C 类 action 不冒充此类发现。

## 首批推荐、处理顺序与验收

首批不调整依赖、事务、权限、事件协议或模块架构。每组独立提交，便于逐项撤回；实施前重新核对 HEAD/用户未提交改动，候选 ID 保持稳定。

| 顺序 | 推荐 ID | 范围与验收条件 |
|---|---|---|
| 1 | CLN-002、003、004、024 | 只删指定私有函数/方法和闲置常量/import；精确符号在源码无消费者、没有补替代旁路；当前路由/协议/权限完全不变；相关后端专项 + 完整 check.sh 通过。 |
| 2 | CLN-005、006、007、025 | 清理孤立导出和旧 Hook 链路，删除失效 mock 但保留其所有测试断言；不删旧浏览器值、不动 reducer/runStatus；lint/typecheck/test/build 通过，普通聊天与任务同步测试保留。 |
| 3 | CLN-001、013 | 只改仓库模板；离线 argparse/空结果输入输出一致，无远端请求，不覆盖运行时 Skill；模板目标 F841/F541 告警归零，其他告警单列。 |
| 4 | CLN-008 | 删除孤立模型组，完整 OpenAPI 前后等价；当前 snapshot/SSE 回归保留并通过。若发现任何导出契约，降为 C，不凑首批数量。 |
| 5 | CLN-009、010、011、012 | 文档/注释与当前实现一致，未验证状态显式保留；只改 ops 输出文案，脚本动作不变；文档链接、部署脚本静态/现有测试通过。 |

B 类后续建议顺序：CLN-017 先恢复生成清单一致性；CLN-015 小范围消除等价重复；CLN-016 单独拆分计划；CLN-018 测量后决定。CLN-014 默认保留差异，不以合并作为成功标准。CLN-019～023 在补证据前不执行。

补充项中 CLN-028 先提交测试覆盖迁移方案，CLN-031 保持时钟调用点不变，CLN-035/036 先核对历史状态与架构规则，再各自独立实施。新增 C 类 CLN-026/027/029/030/032/033/034/037/038 不进入首批。所有新增项沿用下述按提交或精确 hunks 回退方式。

### 总体验收条件

- 原审计轮验收：只有此报告新增，分支/HEAD 不变，业务代码/测试/配置无 diff。该历史验收已完成；本次 A 类实施改动与最新验证另见「首批实施记录」。
- 清理实施验收：`scripts/check.sh` 和 `npm --prefix frontend run build` 通过，逐项写出通过/跳过/预期失败；无专用测试连接时不能宣称数据库验证完成。
- 任何触及数据/锁/恢复/权限的后续方案，必须使用专用测试连接及随机隔离 schema，补齐失败、取消、重试和恢复验证；不允许借业务库、重建表、删拒重键或清理历史作为验收捷径。
- 保持已公开 API/OpenAPI、SSE 类型/帧去重、任务增量与摘要请求范围、Cookie/租户/项目校验、Checkpoint 分工和当前工具审批行为。保持 14px 主要字号，CSS/品牌资源不在首批改动内。
- 测试不随清理批次删除。本报告没有证明任何测试只覆盖已删除行为；若以后删除测试，先逐项列当前行为覆盖去向与旧行为实际删除证据。

### 回退方法

源码/模板/文案清理按组提交；问题出现时只 revert 对应清理提交，保留用户后续改动，不做整树 reset。未提交时按清理补丁逆向恢复指定 hunks。没有 schema/数据变更，无需数据库回滚；模板恢复只影响仓库分发，不碰用户安装副本。

依赖 B 项若单独实施，pyproject/uv.lock/生成 requirements 作为同一变更单元回退，再以 uv sync --locked 恢复环境；不要手工修锁或清业务数据。模块拆分/性能项先留行为和构建基线，收益未证实或复杂度上升则撤回该独立提交。

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-10-17 | 只新增本审计计划，不改 note、技术债清单或其他源码/说明文件 | 本轮用户明确要求以报告结束；发现以候选形式记录，后续另行实施。 |
| 2026-10-17 | 私有小型孤立符号可列 A；公开方法、身份 action、旧审批 UI 列 C | 区分仓库调用证据与外部/恢复契约，保护既有数据和权限行为。 |
| 2026-10-17 | 两种轮询 Hook 保留差异，不扩展轮询范围 | 首次时机、错误撤旧/保留与状态归属不同，共同循环不足以证明可合并。 |
| 2026-10-17 | requirements 漂移单列 B，外部 Skill 依赖裁剪列 C | 生成文件不能手工裁；已有 pandas/示例/框架消费者，外部运行数据未检查。 |
| 2026-10-17 | 不新增或删除测试，不启用扩展 lint 规则 | 本轮只建基线；435 条扩展告警是文档更新线索，不能机械修改异常/取消语义。 |
| 2026-10-17 | 按用户指定顺序实施 B 类 017 → 015 → 036 | 保留锁定版本与已有未提交改动；等待函数离线行为等价，API 导入约束仅收紧；其他候选不实施。 |
| 2026-10-17 | 用户明确授权后实施 A 类 CLN-001～013、024、025，按目的分批且不提交 | HEAD/引用未变化；保留全部 B/C 边界及有效测试，部署脚本只改提示。 |
| 2026-10-17 | 对用户提供的另一份 AI 计划逐项复核，新增 CLN-024～038，保留原 ID | 接受常量、孤立 Hook 和维护漂移事实；纠正错误符号、配置等价假设与未证实覆盖，权限/协议/事务项维持 C。只更新本报告。 |

## 风险与依赖

真实数据库专项、外部 Skill 依赖清单、已有审批 Checkpoint、浏览器 E2E 和生产/CI 证据未验证，是 B/C 项后续决策的明确依赖。A 项不以这些外部系统不存在为前提，也不授权数据清理或行为变更。预期收益主要是减少错误维护面；未测的性能、磁盘、包体积和 CI 时间收益均不量化。
