# MelonClaw 技术演进与设计记录

本文记录 MelonClaw 在新增 Agent 能力、持久化能力和交互能力时形成的设计决策。每一节都尽量说明引入功能的背景、方案、实现位置、验证方式和当前边界，作为后续迭代和排障的依据，而不是一份脱离代码的教程。

参考资料包括：[LangChain 官方 Quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart)、[Tools](https://docs.langchain.com/oss/python/deepagents/tools)、[Skills](https://docs.langchain.com/oss/python/deepagents/skills)、[Interpreters](https://docs.langchain.com/oss/python/deepagents/interpreters)、[Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[Tavily Search SDK](https://docs.tavily.com/sdk/python/reference)；DeepSeek 兼容接口参考：[DeepSeek Claude Code 集成](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)。

## 研究 Agent 能力的引入

为新增研究能力，入口没有从零手写 Agent loop，而是把研究任务交给 `create_deep_agent`：

```python
agent = create_deep_agent(
    model=model,
    tools=[internet_search],
)
result = agent.invoke({"messages": [{"role": "user", "content": question}]})
```

`create_deep_agent` 在这个入口上叠加了 Deep Agents 的 harness 能力：

1. 文件工具：`ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`。
2. `task`：需要时委派给子 Agent。
3. 上下文管理：把过长的搜索材料移到文件中，避免全部挤在对话上下文里。
4. 研究 Agent 的工具组合：让模型先搜索、再整理、最后写报告。

## 工具体系的设计

工具装配按三类来源划分，便于继续增加能力并控制副作用边界：

1. **自定义 callable**：`tool/search.py` 中的 `internet_search` 按用户给出的 Tavily 示例创建 `TavilyClient`，只用类型标注和 docstring 定义工具 schema；`tool/tools.py` 中的 `build_custom_tools` 负责创建并返回这个搜索工具。
2. **MCP 工具**：应用只从根目录 `mcp.json` 读取 MCP 服务定义；`DEEPAGENTS_MCP_SERVER_NAMES` 仅用于从该文件选择服务。存在 `TUSHARE_MCP_TOKEN` 且没有显式选择时，默认选择 `tushare_mcp`。`MultiServerMCPClient.get_tools()` 返回的全部工具会和 Tavily 搜索工具一起注册到 `create_deep_agent(tools=...)`；只有显式配置白名单时才在注册前筛选。
3. **harness 内置工具**：文件管理和 `task` 由 DeepAgents 自动提供；当前文档中 `write_todos` 属于可选任务规划能力，不应假设每个 Agent 默认暴露它。本项目目前用提示词规划研究步骤，但没有额外打开 `TodoListMiddleware`。

工具调用的边界是：模型可以选择工具，但工具本身仍应限制副作用。这里自定义搜索工具不接受文件路径，也不直接写入仓库；MCP 服务则由配置决定外部权限。

Tools 页面还介绍了多模态工具返回值。本项目的研究工具当前只返回文本，`output/content.py` 已按标准 text block 过滤终端输出，`output/streaming.py` 负责流式展示；后续若加入图片、PDF 或音频工具，应优先把大文件放入 backend，只把简短描述和路径/URL返回给模型，避免上下文膨胀。

## Skill 目录与渐进式加载

为避免把完整流程长期塞进每轮上下文，Skill 采用包含 `SKILL.md` 的目录：前置 YAML metadata 用于启动时发现，正文在任务匹配时才读取，脚本、参考资料和模板等支持文件继续按需加载。

新增的 [research-workflow](skills/research-workflow/SKILL.md) 将研究任务需要遵守的流程沉淀为可复用约束：拆分问题、选择最小工具集合、整理证据、标注推断和不确定性，并保护凭据与临时文件。Skill 的 `description` 包含“研究、对比、网页、近期信息”等触发词，方便 Agent 在启动时判断是否需要激活。

### 运行文件后端

当前系统使用 `CompositeBackend`。默认后端是当前进程的临时 runtime，`/skills/` 通过独立路由直接指向仓库根目录下与 `src/` 平级的 `skills/`，因此不会在 `temp/melonclaw-*/skills/` 创建副本：

```text
/skills/**  ──>  skills/                  （仓库中的持久化项目知识）
其他路径   ──>  temp/melonclaw-*/         （运行时中间文件）
```

Agent 通过 `skills=["/skills/"]` 注册这个虚拟源。Deep Agents 的 `SkillsMiddleware` 负责扫描子目录中的 `SKILL.md`、注入 metadata，并在模型决定使用时让它调用 `read_file` 读取正文。这样新增 Skill 只需要在 `skills/` 下增加一个目录和合法 frontmatter，不需要再修改 Agent 组装代码。

项目 Skill 的源文件通过 `CompositeBackend` 的 `/skills/` 路由直接提供，并由 `FilesystemPermission(mode="deny")` 拒绝写入；研究中间文件继续写入临时 runtime。这里仍是受信任的本地开发系统，默认后端 `LocalShellBackend` 的 shell 能力本身不是安全沙箱，生产服务不能把 Web 入口暴露给不受信任用户。

当前默认使用 `.env` 中的 DeepSeek 作为模型、Tavily 作为搜索服务。DeepSeek 仍通过 `ChatOpenAI` 访问其 OpenAI 兼容接口；搜索则由 `TavilyClient.search()` 执行，结果作为普通 callable 的返回值交给 DeepAgents。这样可以清晰观察“模型决定调用工具、工具负责外部 API、副作用由工具边界控制”的完整链路。

## 设计取舍

- 配置与 Agent 组装分离：模型配置在 `core/config.py` / `core/model.py`，MCP 服务选择、占位符展开与凭据脱敏在 `core/mcp_config.py`；工具装配和提示词不直接读取密钥文本。`build_system_prompt()` 在 Agent 启动时按运行机器本地时区注入当天 ISO 日期，使模型能够解析“今天”“昨天”等相对日期；跨过午夜的长驻进程需要重启后才会刷新该日期。
- MCP 适配器是由 `pyproject.toml` 和 `uv.lock` 固定的必需依赖，`tool/tools.py` 直接在模块顶部导入 `MultiServerMCPClient`。若环境缺包，让原始 `ModuleNotFoundError` 直接暴露并通过 `uv sync` 修复；不再用运行期 `try/except ImportError` 把它伪装成可选依赖。
- 搜索工具独立：`tool/search.py` 创建 Tavily client 并返回 `internet_search` callable；查询限制在工具边界内校验，避免把超长研究提示词直接当作搜索 query。
- 本地 Shell 后端：`LocalShellBackend(..., virtual_mode=True)` 让文件工具使用项目根目录 `temp/` 下的临时 runtime，并让 `execute` 在该目录作为工作目录执行；但 Shell 命令本身可以访问宿主机任意路径，因此它只适合受信任的本地 CLI，生产 Web 服务不应直接暴露这个后端。文件工具必须使用 `/summary.md` 等虚拟路径，不能把终端中显示的 runtime 宿主路径传回 `write_file` 或 `read_file`。
- 当前 CLI 是多轮交互模式并通过 `output/events.py` 消费 `agent.astream_events(version="v3")`（旧运行时才降级到 `astream()`）；MCP 适配器生成的 `StructuredTool` 是异步工具，因此 Agent 构建、MCP 工具发现、模型流和工具调用都在同一个 asyncio 事件循环中执行。`main()` 只在最外层调用一次 `asyncio.run(run_application())`，内部依次 `await build_research_agent()`、`await build_agent_tools()` 和 `await load_mcp_tools()`，不再维护同步包装器。
- Skill 目录是持久化的项目源文件，运行时通过 `/skills/` 单独路由到它；Agent 只能读取 Skill，研究中间文件仍通过默认后端写入临时 runtime。Skill 正文采用短而明确的步骤，未来较大的参考资料应放入该 Skill 的 `references/`，并在 `SKILL.md` 中显式引用。
- 不默认接入 LangSmith：官方页面把 tracing 作为可选步骤，项目先保持最小可运行面；如果配置 tracing，可由 LangSmith 环境变量自动接管。

## Interpreters：在 Agent loop 内用代码编排工作

普通工具调用的控制流由模型逐轮决定：模型发出一批固定调用，等待结果进入消息上下文，再决定下一批。如果任务需要根据结果循环、分支、重试或聚合，这会增加模型往返次数，还会让大量中间结果占用上下文。Deep Agents Interpreters 把这一段控制流移进轻量代码运行时：模型调用一次 `eval` 并提交 JavaScript，QuickJS 执行程序，只把最终结果和有限的 console 输出送回模型。

它与现有 `LocalShellBackend.execute` 的定位不同：

| 能力 | `eval` Interpreter | `execute` / Sandbox backend |
| --- | --- | --- |
| 主要用途 | Agent loop 内的循环、分支、并发与结构化数据变换 | 操作运行环境、执行命令、安装依赖、运行测试 |
| 默认宿主能力 | 无文件、网络、Shell、包管理器和真实时钟 | 取决于 backend；当前本地 Shell 可访问宿主机 |
| 应用审批 | `eval` 自身不审批，但只开放受限能力 | `execute` 与写文件进入 HITL |
| 状态 | `mode="thread"` 时可随会话 checkpoint 恢复 | 文件状态由 backend 生命周期决定 |

项目在 `core/interpreter.py` 集中创建 `CodeInterpreterMiddleware`，再由 `core/agent.py` 和已有自定义 middleware 一起交给 `create_deep_agent`。参数选择如下：

- `mode="thread"`：同一 `thread_id` 的可序列化 JavaScript 状态跨轮保存；CLI 的固定 thread 和 Web 的 conversation thread 都由 PostgreSQL Checkpointer 持久化。
- `memory_limit=64 MiB`、`timeout=15s`、`max_result_chars=4000`：限制同进程 QuickJS 工作区的内存、单次执行时间和返回上下文体积。Interpreter 是同进程 capability boundary，不是进程/虚拟机级内存隔离；不受信任场景仍应把 Agent 放进隔离 worker 或容器。
- `ptc=["internet_search"]`、`max_ptc_calls=8`：通过 Programmatic Tool Calling 只桥接只读搜索，让 JavaScript 可以循环或 `Promise.all` 多次搜索。工具在解释器中按 camelCase 暴露为 `tools.internetSearch({...})`，参数仍遵守原工具 schema。
- `subagents=True`：存在 Deep Agents `task` 工具时，Interpreter 可以用顶层 `task({...})` 编排 `general-purpose` 子 Agent。它与 `tools.*` PTC 是两条不同桥接路径；子 Agent 仍继承应用现有的 `interrupt_on` 配置。

PTC 是明确的权限边界。它的内部调用不会逐次经过父 Agent 的 `interrupt_on` / ToolNode，因此当前不把 `write_file`、`edit_file`、`delete`、`execute` 或 MCP 工具加入白名单。未来增加任何付费、写数据库、发消息、交易或部署工具时，也不能仅因为“主 Agent 已配置 HITL”就把它放进 PTC；应先确认无需逐次审批，或改为直接工具调用。

### 运行与观察

依赖由 `langchain-quickjs==0.3.5` 提供；`deepagents==0.7.5` 的 `quickjs` extra 要求至少 0.3.3。由于 Interpreter 仍是 Beta API，应用把已验证版本精确锁定，升级时应重新执行本节的状态与 PTC 验证：

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw
```

纯内存验证不需要 Tavily 请求：

```text
🧑> 请务必使用 eval 计算 1 到 100 的平方和，不要使用 execute。
```

预期看到 `eval` 工具调用并得到 `338350`，不会出现 Shell 审批。连续两轮让 `eval` 写入和读取 `globalThis.studyTopic`，可观察同一 thread 的状态恢复；新建 Web conversation 后同名变量应不存在。需要观察 PTC 时，可以要求 Interpreter 并行搜索 2～3 个主题并只返回标题去重结果；工具时间线的外层是 `eval`，最终进入模型上下文的是整理后的有限结果，而非模型逐轮接收每次搜索的全部中间值。

## Human-in-the-loop：暂停、人工决定与恢复

Deep Agents 将 HITL 放在模型已经提出工具调用、工具尚未执行的边界：`HumanInTheLoopMiddleware` 产生 LangGraph interrupt，调用方读取 checkpoint 中的 `action_requests`，再以 `Command(resume={"decisions": [...]})` 恢复同一执行。这里不能只在终端里打印“是否同意”，因为没有把决定传回同一个 checkpoint 的话，工具调用不会继续；也不能缺少 checkpointer，interrupt 没有可恢复的位置。

CLI 和 Web 都把同样的 Agent 接到 PostgreSQL `AsyncPostgresSaver`。CLI 固定使用 `deepagents-quickstart` 作为 `thread_id`，Web 使用经过归属校验的 `conversation_id`，因此业务会话和 HITL 中断都可以跨后端重启继续：

```text
模型提出工具调用
        ↓
HumanInTheLoopMiddleware 暂停并持久化到 PostgreSQL Checkpointer
        ↓
main.py 从 await agent.aget_state(config).interrupts 读取 action_requests
        ↓
用户 approve / edit / reject / respond
        ↓
Command(resume={"decisions": [...]}) 用相同 thread_id 恢复
```

### MelonClaw 中的策略

`LocalShellBackend` 支持 `execute`，但当前 Deep Agents 不允许它与覆盖路径的 `FilesystemPermission` 一起使用，因此应用只使用 `core/hitl.py` 中的 `interrupt_on` 审批配置：运行时 `write_file`、`edit_file`、`delete` 和 Shell `execute` 都在真正执行前暂停。它保留了 CLI/Web 的一致行为，但不提供沙箱隔离；任何部署到共享环境前都必须更换为受隔离的 SandboxBackend。

文件权限不覆盖 Shell 命令，故 `core/hitl.py` 另外以 `interrupt_on` 注册 `execute`。当前应用的 Tavily 搜索和 Tushare MCP 查询均按只读用途接入，未加入审批清单；以后若加入发邮件、数据库写入、交易或部署工具，应将工具名加入 `SENSITIVE_TOOL_INTERRUPTS`，并按风险缩小 `allowed_decisions`。

CLI 的 `aget_pending_approval()` 在每次 `astream()` 完成后异步读取 `agent.aget_state(config).interrupts`，不依赖特定模型供应商的流式 token 形状。`request_human_decision()` 展示经过现有脱敏函数处理的参数，并构造恢复命令。支持：

1. `approve` / `a`：执行原调用。
2. `edit` / `e`：接收完整 JSON 参数替换，并固定原工具名，避免编辑时跳转到未审工具。
3. `reject` / `r`：不执行，拒绝原因成为模型可见的 ToolMessage。

实现保留了 `respond` 的通用解析能力，便于将来接入“向用户提问”类工具；当前所有副作用工具都只允许前三种决定，避免把模拟成功结果误用于拒绝操作。

CLI 和 Web 都使用 PostgreSQL Checkpointer；Web 由服务端先校验 user_id 与 conversation_id，再按原 thread 恢复。仅把 thread_id 写入数据库并不能替代归属校验。

## 文件工具的依赖排序 Middleware

LangGraph 的 ToolNode 默认并发执行同一条 AIMessage 内的工具调用。于是模型若一次同时调用 `write_file('/summary.md')` 和 `read_file('/summary.md')`，读取可能先于写入，出现“文件不存在”；这不是虚拟路径后端的读写不一致。

`middleware/file_ordering.py` 的 `FileOperationOrderingMiddleware` 在 `after_model` 钩子检查该批工具调用：收集 `write_file`、`edit_file`、`delete` 的 `file_path`，再找出读取相同路径的 `read_file`。命中时它会从 AIMessage 中移除读取调用，并添加带相同 `tool_call_id` 的 error ToolMessage，明确要求模型等到写操作结果返回后再单独读取。写操作仍照常进入 HITL 审批与执行；不同路径的读取与其他无依赖调用没有被串行化。

这比只靠提示词可靠：即使模型再次在一个 response 中生成写后读，中间件也不会把读取送进并行 ToolNode。它不猜测跨路径的语义依赖；如需数据库事务、跨文件构建链或多步骤部署，应为那些领域操作设计专门的工作流或 middleware。

## 运行方式

在项目根目录执行：

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw
```

启动后输入问题：

```text
🧑> 请研究 Deep Agents 的文件系统工具和子 Agent 是如何协作的。
```

## Web 聊天区的整屏布局与滚动边界

### 背景与目标

会话数量和聊天消息持续增加时，页面不能被内容整体撑高。左侧会话列表需要在固定的侧栏高度内独立滚动，会话项保持固定行高；右侧输入框需要始终停留在窗口底部，消息过长时只滚动聊天内容。

### 方案概览

`web/static/styles.css` 将桌面端 `.app-shell`、`.sidebar` 和 `.main-panel` 约束到视口高度，并在可滚动的 `.conversation-list` 与 `.conversation` 上设置 `min-height: 0`，让 Flex 子项可以在父容器内收缩。会话列表使用 `grid-auto-rows: 42px` 与 `align-content: start`，避免 CSS Grid 的自动轨道在剩余空间中被拉伸。`.conversation` 是右侧唯一的消息滚动容器；审批区和 `.composer-wrap` 作为不可伸缩的底部区域，输入框不会随消息高度被顶出布局。

窄屏继续保留原有的纵向页面流，聊天主面板自身保持视口高度，避免在移动端将完整侧栏压缩到不可用；进入主面板后仍由聊天区承载消息滚动。

### 关键设计选择

- 使用 `height: 100dvh`、`overflow: hidden` 和 `min-height: 0` 划定桌面端的视口边界，防止浏览器页面滚动接管聊天内容。
- 会话行显式固定为 42px，标题与时间均限为单行，超长标题以省略号展示，分页按钮仍位于列表滚动区下方。
- `overscroll-behavior: contain` 避免内部列表滚动到边界时把滚动链传递给页面；`scrollbar-gutter: stable` 减少聊天滚动条出现/消失造成的横向抖动。
- 保留现有 `app.js` 的“接近底部才自动跟随”逻辑；布局改动只改变滚动容器边界，不强制覆盖用户查看历史消息时的位置。

### 数据/事件流

消息仍由 `app.js` 追加到 `#conversation`，流式事件通过现有 `scrollConversationToBottom()` 只更新聊天容器的 `scrollTop`。会话列表仍由 `renderConversationList()` 追加和分页，列表自身处理滚动；审批事件继续渲染到 `#approval-slot`，不再参与消息列表滚动。

### 失败与安全边界

本次只调整静态布局和缓存版本号，不新增接口、持久化字段、工具权限或外部写操作。移动端仍允许页面纵向滚动以访问顶部侧栏，桌面端要求浏览器支持 `dvh`；不支持时会回退到现有的 `vh`/`min-height` 行为。

### 运行步骤与预期结果

```bash
uv run melonclaw-web
```

打开 Web 页面并创建多条会话、发送足够长的消息：左侧会话项行高保持不变，左侧仅列表区域出现滚动；右侧滚动条只影响消息区，输入框保持在底部。

### 验证记录

- 已检查 CSS Flex/Grid 约束：桌面端根布局、侧栏和主面板均有固定视口高度，可滚动子项设置 `min-height: 0`。
- 已执行 `node --check src/melonclaw/web/static/app.js` 与 `git diff --check`。
- 尚未连接真实 PostgreSQL/模型服务做端到端流式验证；该变更不触及后端协议。

## Web 交互：SSE 流式 Agent 与浏览器审批

原来的 `main.py` 仍保留 CLI，新增平行入口 `src/melonclaw/main_web.py`。它启动 `FastAPI + Uvicorn` Web 服务。FastAPI 负责应用生命周期、路由和请求体模型，底层仍复用 Starlette 的响应、静态文件和 ASGI 能力；浏览器不再等待一次完整结果，而是通过 `POST /api/conversations/{id}/messages` 的 SSE 响应逐事件接收：

```text
用户消息 → user_id + conversation_id → agent.astream_events(version="v3")
                                      ├─ text / tool_call / tool_result
                                      ├─ subagent_started → subagent_text/tool → subagent_completed
                                      └─ approval_required → completed → done
```

运行方式：

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw-web
# 等价入口：uv run python -m melonclaw.main_web
```

打开 <http://127.0.0.1:8000> 即可。`MELONCLAW_HOST` 和 `MELONCLAW_PORT` 可以覆盖监听地址与端口，例如：

FastAPI 自动生成的接口文档位于 <http://127.0.0.1:8000/docs>，其中可以查看 `MessageRequest`、`ApprovalRequest` 等请求体模型。

```bash
MELONCLAW_HOST=0.0.0.0 MELONCLAW_PORT=8080 uv run melonclaw-web
```

默认服务只监听本机且没有登录鉴权；绑定局域网或公网前，应在反向代理或应用层补充鉴权与 HTTPS。

### Web 层的职责边界

- `web/app.py` 只负责 FastAPI HTTP 路由、Pydantic 请求体校验和 SSE framing；它不理解 LangChain 消息细节。
- `web/service.py` 从数据库解析开发用户和租户，校验 Project 归属，管理持久化 conversation 和同一 `thread_id` 的串行访问；同一 Project 的会话复用指向该 Project 工作目录的 Agent backend。消息和审批恢复都经过 PostgreSQL advisory try-lock，锁连接跨整个 Agent 执行持有，避免多进程同时提交同一会话的两轮模型调用。
- `core/database.py` 使用 SQLAlchemy asyncpg 访问租户、用户、用户租户关系、`projects`、`chat_conversations` / `chat_messages`，并从同一 `DATABASE_URL` 派生 psycopg URL 创建 `AsyncPostgresSaver` 连接池；业务历史查询不读取 Checkpointer 内部表。
- `db_init.py` 是独立初始化入口，先执行幂等业务迁移和演示数据种子，再执行 Checkpointer `setup()`；API lifespan 只打开池并检查表，不在多个 worker 启动时并发迁移。
- `output/events.py` 优先并发消费 `agent.astream_events(version="v3")` 的 `messages`、`tool_calls` 和 `subagents` 投影，将命名空间、父工具调用 ID 和状态转成 CLI/Web 共用的结构化事件；没有 v3 的旧运行时才降级到 `astream(stream_mode="messages")`。工具参数、结果和子 Agent 文本在离开后端前会限长并脱敏。
- `web/app.py` 保持原生 SSE framing，并为长任务发送注释心跳、递增 SSE `id` 和传输错误终止事件；`web/static/app.js` 使用原生 `fetch` reader 解析，不依赖 React 或 LangChain 前端 SDK。
- `core/hitl.py` 的 `serialize_pending_approval()` 只把脱敏后的参数发到浏览器；`build_resume_command()` 会校验决定数量、允许的决定类型，并禁止编辑审批时替换工具名称。

CLI 和 Web 遵循同一套 Agent 组装配置，都使用 `CompositeBackend` 和 `/skills/...` 路由；CLI 的默认 `LocalShellBackend` 根目录位于临时 runtime，Web 则按 Project 缓存 Agent，并把默认 backend 根目录切换到持久 Project workdir。Skill 写入由权限规则拒绝，其他文件写入和 Shell 执行都经过 HITL；由于 LocalShellBackend 没有沙箱隔离，Web 默认只监听本机，不能作为面向不受信任用户的生产服务。Agent 的状态由 PostgreSQL Checkpointer 跨重启保存，Web Project 文件不会写进仓库。

### Web 运行时的可观察点

- 首屏显示 provider、模型和 MCP 服务状态；不会显示任何 API Key、MCP URL 或 token。
- 助手回复旁会出现可折叠的工具时间线；`task` 工具下面会嵌套 `general-purpose` 子 Agent 卡片，显示运行/完成/失败状态、文本进度、工具数量和限长结果。
- 前端只在用户接近会话底部时自动跟随流式输出；工具参数/结果和已完成子 Agent 默认收起，长任务不会把历史阅读位置强行拉到底部。流末尾会 flush `TextDecoder`，缺少 `done` 时也会解除输入框锁定并提示刷新。
- 触发 `write_file`、`edit_file`、`delete` 或 `execute` 时，流会以 `approval_required` 暂停；页面提交决定后，用相同 `thread_id` 发送 `Command(resume={"decisions": [...]})` 继续。
- 点击“新建对话”会生成新的 UUID conversation/thread；同一 Project 下的多个 conversation 共享 Project workdir 文件，但各自的多轮消息、todo 状态和 Checkpointer 状态互相隔离。
- 页面模拟用户来自数据库的 `users` 与 `user_tenants` 关系；一个用户只生成一个下拉选项，多个租户名称由后端以“、”连接后返回。后端将张三标记为默认用户并置于第一项，前端首次加载默认选中张三。前端不维护用户或租户名单，只使用接口返回的 `display_name`、`tenant_ids` 和 `default_tenant_id`。租户只是用户标签，切换用户不会切换该用户的 Project 或 Conversation。模拟用户下面先显示“项目”文件夹列表，并在同一行提供“新增项目”；点击文件夹后进入该 Project 的聊天会话列表，再次点击已打开的文件夹即可收起会话，Project 不再作为下拉筛选器。所有按 conversation_id 的接口都按 `user_id + project_id` 再次校验归属，不存在或归属不匹配统一返回 404。它不是认证机制，上线时应由认证上下文产生 user_id。
- 前端 localStorage 只保存当前用户、租户标签、Project 和会话提示；恢复时仍由后端按 user_id/project_id 校验，切换用户/会话会取消旧请求并丢弃迟到流事件。

配置 Tushare token 后查询金融数据：

```text
🧑> 请查询上交所 2026-01-05 是否开市，只使用 Tushare 数据。
```

如果要通过环境变量切换模型 provider（搜索仍使用 Tavily）：

```dotenv
OPENAI_API_KEY=你的 OpenAI API Key
OPENAI_MODEL=支持 structured output 的 OpenAI 模型名
```

```bash
DEEPAGENTS_PROVIDER=openai uv run melonclaw
```

## 预期结果与可观察点

- 终端先显示当前 provider、模型名和项目 `temp/` 下的受限临时 runtime 路径；不会显示任何 API Key。
- 终端显示 `已启用 Agent Skill: /skills/`；研究类问题时，模型应先读取 `research-workflow/SKILL.md`，然后按其中流程选择工具和组织答案。
- Agent 通常会调用 Tavily 的 `internet_search`，随后可能调用文件工具或 `task`，最后输出中文研究报告。
- 中间文件若被 Agent 写入，会出现在终端显示的 `temp/melonclaw-xxxx/` runtime 目录，可用来观察上下文卸载。
- Agent 使用的临时 runtime 目录会在进程退出时清理。
- 配置 MCP 后，终端会显示服务名和注入的工具名，但不会显示 JSON 中可能存在的认证信息；MCP 工具执行失败默认作为错误 ToolMessage 返回给模型，连接和发现失败会抛出已脱敏的错误。
- 交互模式会持续打印模型文本、工具调用及工具结果，例如 Tushare 工具的结构化参数和返回数据。
- 如果 Tavily API Key 缺失或搜索请求失败，程序会在构建/工具调用阶段给出错误；请检查 `.env` 中的 `TAVILY_API_KEY` 和 Tavily 账户额度。
- 当 Agent 提出写入、编辑、删除运行时文件或执行 Shell 命令时，工具不会立即运行；终端先出现 `🛡️ [需要人工审批]`，包含工具名和脱敏后的参数。输入 `a` 后才会看到 `🧰 [工具结果]`；输入 `r` 时不会产生文件或命令副作用，Agent 会根据拒绝原因继续回答或重新规划。写入 `summary.md` 的正确虚拟路径是 `/summary.md`，而非宿主机绝对路径；验证新写入的文件时必须等待写入工具返回成功，再发起下一轮 `read_file`，不能在同一批工具调用中并发读取。
- CLI 在审批提示处 `Ctrl-C`/EOF 会保留本次进程内的暂停 checkpoint 且不执行操作；Web 审批使用 PostgreSQL Checkpointer，可在服务重启后按同一 conversation 恢复。两者仍都要求通过原有 `Command(resume=...)`，不会把审批误当作新的用户消息。

## MCP Server/Client 集成与验证

MCP 集成需要明确三个边界：Server 暴露工具，Client 负责发现和调用工具，传输层决定两者如何通信。`example/mcp/` 用一个数学 Server 把这条链路具体化：

```text
example/mcp/
├── server/math_stdio_server.py  # 只负责启动 STDIO Server
├── server/math_sse_server.py    # 只负责启动 SSE Server
└── client/mcp_client.py     # 读取配置、发现工具、调用工具
```

项目根目录的 [mcp.json](mcp.json)（与 `src/` 同级）是多个 MCP 服务的配置入口。文件采用常见的 `mcpServers` 顶层键和 `type` 连接类型，便于复制到支持该格式的其他客户端；项目加载器仍兼容旧的 `servers` 和 `transport`。暂时不用 MCP 时，可以将文件设为 `{}` 或把整段 JSON 用 `//` 注释掉，MelonClaw Agent 会按没有 MCP 正常启动。

### Server

`math_stdio_server.py` 和 `math_sse_server.py` 都是可独立阅读和运行的完整 FastMCP Server，各自注册 `add`、`multiply` 和 `explain_transport` 三个工具。两份少量重复代码是有意的：理解任一传输时不需要跳转到共享工厂模块。STDIO Server 名为 `MCP Study Math STDIO`，SSE Server 名为 `MCP Study Math SSE`。STDIO Server 由 Client 启动本地子进程，协议消息走 stdin/stdout，因此不向 stdout 写普通日志：

```bash
uv run python example/mcp/server/math_stdio_server.py
```

SSE 模式启动 HTTP Server，默认端点是 `/sse`：

```bash
uv run python example/mcp/server/math_sse_server.py --port 8000
```

SSE 适合本地验证和兼容已有服务；对于新的 HTTP 服务，MCP 文档建议优先考虑 `streamable-http` 传输。

### Client 与 mcp.json

`client/mcp_client.py` 不把 Server 地址和启动命令写死在代码里，而是读取根目录 [mcp.json](mcp.json) 的 `mcpServers`，把每个服务的 `type` 规范化为 LangChain 适配器使用的 `transport`，再一次性注册到同一个 `MultiServerMCPClient`。加载器也接受旧的 `servers` + `transport`，但禁止两个顶层键同时出现，避免配置来源含糊。`--server` 只决定本次从哪个已注册服务发现和调用工具；省略时默认使用 `math_stdio`。`math_stdio` 配置 `uv run python example/mcp/server/math_stdio_server.py`，`math_sse` 配置 `http://127.0.0.1:8000/sse`。

STDIO 不需要手动启动 Server，Client 会按配置启动子进程：

```bash
uv run python example/mcp/client/mcp_client.py --server math_stdio --list-tools
uv run python example/mcp/client/mcp_client.py --server math_stdio --tool add --arguments '{"a": 7, "b": 5}'
```

SSE 需要先在一个终端启动 Server，再在另一个终端使用 Client：

```bash
uv run python example/mcp/client/mcp_client.py --server math_sse --list-tools
uv run python example/mcp/client/mcp_client.py --server math_sse --tool multiply --arguments '{"a": 6, "b": 7}'
```

`MultiServerMCPClient.get_tools()` 会把 MCP 工具转换为 LangChain 工具。按照参考文档，它默认是无状态的：每次工具调用会创建新的 MCP ClientSession；如果 Server 需要跨调用保持会话，应改用 `client.session()` 显式管理生命周期。

### Tushare 远程 HTTP MCP

来源：[Tushare MCP 配置与使用](https://tushare.pro/document/1?doc_id=463)、[LangChain MCP 文档](https://docs.langchain.com/oss/python/langchain/mcp)。

Tushare 官方给出的配置使用常见的 `mcpServers` 顶层键，并把凭据放在远程 URL 中。当前实现保留这一顶层形状，显式增加 `type: "http"`，加载时再转换成 `MultiServerMCPClient` 使用的 `transport: "http"`。

仓库只保存环境变量占位符：

```json
{
  "mcpServers": {
    "tushare_mcp": {
      "type": "http",
      "url": "https://api.tushare.pro/mcp/?token=${TUSHARE_MCP_TOKEN}"
    }
  }
}
```

真实 token 只放在被 Git 忽略的 `.env`。`mcp_client.py` 读取 JSON 后递归展开 `${VARIABLE_NAME}`，把 `type` 转换为 `transport`，再执行 URL 校验；变量缺失或为空时只报告变量名，不回显 URL。解析器还接受省略类型但只提供 `command` 或 `url` 的常见配置，并分别推断为 `stdio` 或 `http`。这个解析器不包含 Tushare 专属分支，因此以后也能用于 MCP headers 或其他服务的字符串配置。

MelonClaw 应用复用了同一个 `mcp.json` 服务目录，但把应用侧职责放在 `core/mcp_config.py`：

1. MCP 服务定义只保存在 `mcp.json`；`.env` 中的 `TUSHARE_MCP_TOKEN` 只用于展开该文件中的 `${TUSHARE_MCP_TOKEN}` 占位符。
2. `.env` 存在 `TUSHARE_MCP_TOKEN` 且未设置 `DEEPAGENTS_MCP_SERVER_NAMES` 时，自动选择 `tushare_mcp`。
3. 只展开选中服务中的环境变量占位符，因此未启用的服务不会误报缺少凭据。
4. `tool/tools.py` 用 `MultiServerMCPClient.get_tools()` 把远程工具转换为 LangChain tools，`core/agent.py` 再把它们连同 `internet_search` 一次性交给 `create_deep_agent`。
5. 模型拿到的不只是工具名称和描述，还包括 MCP Server 返回的输入 schema，所以可以生成 `exchange`、`start_date`、`end_date` 等结构化参数。
6. `core/prompts.py` 始终把应用启动时的本地日期写入系统提示词；启用 `tushare_mcp` 时再加入金融数据调用约束，要求不猜参数、不虚构结果，并明确日期和口径。
7. 远程发现实际返回 250 个 Tushare 工具。应用默认全部注册；`DEEPAGENTS_TUSHARE_MCP_TOOLS` 仅作为可选白名单，填写逗号分隔的名称时缩小候选目录，未设置、空值或 `*` 都表示注册全部工具。
8. 251 份完整工具 schema 不会一次性交给主模型。`provider=openai` 时使用 LangChain 官方 `LLMToolSelectorMiddleware(max_tools=16)`，由 OpenAI 原生 structured output 返回工具名称；`provider=deepseek` 时使用项目自定义 `CatalogToolSelectorMiddleware(max_tools=16)`，因为当前 DeepSeek 兼容接口会拒绝官方 selector 默认使用的 `response_format=json_schema`。自定义路径改用无 function tools 的普通模型调用返回 JSON 文本，再通过 `ModelRequest.override(tools=...)` 只把最多 16 个完整工具 schema 交给主模型。解析成功的 `{"tools": []}` 会作为“本轮不需要应用工具”保留，不会触发失败兜底或打印选择日志；只有响应无法解析且提取不到合法工具名时，才使用目录前 16 项兜底。两条路径共享完整工具目录，并在 Agent 组装层按 `Settings.provider` 切换。

启动 MelonClaw：

```bash
uv run melonclaw
```

可观察的启动信息类似：

```text
已启用 MCP 服务: tushare_mcp
已注入 Agent 工具: 251 个（前 12 个：internet_search, stock_basic, trade_cal, ...）
每轮主模型动态选择工具上限: 16
动态工具选择器: 项目自定义 CatalogToolSelectorMiddleware

🧭 [动态工具选择] trade_cal
```

随后在 `🧑>` 提示符输入自然语言问题即可由选择器从完整 Tushare 工具目录中筛出本轮候选，再由主模型调用。设置 `DEEPAGENTS_PROVIDER=openai` 后，启动日志中的选择器会变成 `LangChain 官方 LLMToolSelectorMiddleware`；默认 DeepSeek 路径仍显示项目自定义选择器，并额外打印 `🧭` 选择结果。若要显式启用、组合或关闭 `mcp.json` 服务，可在 `.env` 中设置逗号分隔的 `DEEPAGENTS_MCP_SERVER_NAMES`；空值表示关闭自动选择。若以后需要在选择前缩小 Tushare 工具目录，可设置例如 `DEEPAGENTS_TUSHARE_MCP_TOOLS=stock_basic,trade_cal,adj_factor,daily`。

端到端验证使用问题“请查询上交所 2026-01-05 是否开市，只使用 Tushare 数据”。DeepSeek 自动生成并执行了下面的工具调用：

```text
🔧 [工具调用] trade_cal
   参数: {"exchange": "SSE", "start_date": "20260105", "end_date": "20260105"}

🧰 [工具结果] trade_cal
   [{"exchange": "SSE", "cal_date": "20260105", "is_open": 1, "pretrade_date": "20251231"}]
```

该验证也明确了同步/异步边界：若用 `agent.stream()`，LangGraph 会同步调用 MCP `StructuredTool` 并报“不支持同步调用”；因此应用把异步边界提升到 CLI 最外层，只调用一次 `asyncio.run()`，让 Agent 构建、工具发现、模型流和 MCP 工具运行在同一个事件循环中。工具选择、参数生成、远程调用和最终回答由此形成完整闭环。

首先执行工具发现：

```bash
uv run python example/mcp/client/mcp_client.py \
  --server tushare_mcp \
  --list-tools
```

可观察的数据流是：`mcp.json` → 从 `.env` 展开占位符 → 配置校验 → `MultiServerMCPClient.get_tools()` → 远程工具名和描述。工具名称与 schema 由远程 Server 返回，文档不预先猜测；发现后再用通用的 `--tool` 和 `--arguments` 调用。实际发现确认服务包含 `stock_basic`、`trade_cal`、`daily` 等工具；下面用单个历史日期做最小只读调用：

```bash
uv run python example/mcp/client/mcp_client.py \
  --server tushare_mcp \
  --tool trade_cal \
  --arguments '{"exchange":"SSE","start_date":"20260105","end_date":"20260105"}'
```

该次验证返回上交所 `20260105` 为开市日，并给出前一交易日；这同时证明配置展开、远程工具发现和参数化调用三段链路均可用。

`MultiServerMCPClient` 在这里仍是无状态用法：工具发现和后续工具调用各自建立会话。Tushare 把 token 放在 URL 路径片段中，使用方便但容易被异常堆栈或 HTTP 日志带出，因此 CLI 在输出 traceback 前会同时脱敏 `/token=...` 片段和当前 `TUSHARE_MCP_TOKEN` 的值。生产系统还应避免在代理、APM 和访问日志中记录完整请求 URL。

预期结果与排障边界：

- 成功时，`--list-tools` 至少输出一个由 Tushare MCP 返回的工具名称及描述，不显示配置 URL 或 token。
- `.env` 缺少 `TUSHARE_MCP_TOKEN` 时，Client 在联网前停止并仅提示缺少该变量。
- 远程服务、网络或账户权限异常时，Client 保留异常类型和调用栈用于排障，但其中的 token 会显示为 `<redacted>`。
- 离线测试使用测试专用值，不访问 Tushare；远程冒烟验证失败不会改变配置解析和脱敏测试的有效性。

## Yuxi 用户/组织 Workspace 与 MelonClaw 个人/租户 Memory 设计

记录日期：2026-09-07。参考 [Deep Agents Memory](https://docs.langchain.com/oss/python/deepagents/memory)、[Backends](https://docs.langchain.com/oss/python/deepagents/backends) 和 [Going to production](https://docs.langchain.com/oss/python/deepagents/going-to-production)。本文记录相关设计取舍和当前 MelonClaw 的实现进展。

### 1. 先给结论

Yuxi 的实际边界需要先澄清：它有持久化的 **UserWorkspace**，但当前代码没有独立的“组织文件 Workspace”。组织主要参与用户身份和资源共享范围匹配；真正的文件根按用户 `uid` 隔离。Yuxi 的 Memory 也是用户级的 `agents/MEMORY.md`，不是组织共享记忆。

因此，MelonClaw 不应把“租户标签”简单替换成文件路径，也不应把 LangGraph checkpoint 当成长期记忆。建议采用四层模型：

| 层 | 作用 | 默认可见范围 | 事实存储 |
| --- | --- | --- | --- |
| Thread state | 当前对话、待办、工具状态和 HITL 恢复 | 一个 thread | 现有 PostgreSQL Checkpointer |
| User memory | 用户明确要求长期保留的偏好、稳定背景和工作习惯 | 当前用户 | PostgreSQL Memory Store，必要时投影为 `MEMORY.md` |
| Tenant memory | 租户成员共同使用的明确发布事实、流程、术语和默认约束 | 当前租户的有效成员 | PostgreSQL Memory Store；写入由租户管理员或受控应用流程完成 |
| Project/episodic memory | 当前项目结论或历史对话检索 | 当前用户的 Project | Project 作用域记录 + 现有 `chat_messages` |

其中，租户 Memory 是在 Yuxi 经验上为 MelonClaw 增加的能力，不是 Yuxi 当前已有的组织文件 Workspace。当前 MelonClaw 的 `tenant_id` 只是用户可选择的标签；实现租户 Memory 后，只有经过 `user_tenants` 校验的成员才能读取对应租户命名空间。这个差异必须写进产品和权限契约，避免把“租户可以共享记忆”理解成“租户成员可以读取彼此的个人文件”。

### 2. Yuxi 的真实设计

#### 2.1 用户、组织和资源权限

Yuxi 的 `User` 记录包含 `uid`、角色和组织归属字段；组织实体只保存组织元数据及用户关系。Agent、Skill、知识库等可共享资源使用 version 2 的 `share_config`，读取范围和管理范围分别支持全局、组织、用户。创建者和 `superadmin` 有特殊管理权限，普通 `admin` 不会因为角色名称自动获得所有资源。

这套设计的关键不是前端隐藏入口，而是后端在 repository/service 查询处重新计算有效权限。组织 ID 只是匹配条件，不能作为用户提交的可信授权声明。

#### 2.2 UserWorkspace 和 Project Workdir

Yuxi 的持久目录由 `get_user_data_dir()/shared/<safe_uid>/workspace` 映射到用户。`safe_uid` 对不适合做文件名的身份标识使用稳定哈希，避免身份字符串直接形成路径。Workspace 下会初始化：

```text
agents/AGENTS.md    Agent 行为补充约束
agents/USER.md      用户背景信息
agents/MEMORY.md   用户主动维护的长期记忆
projects/<uuid>/    Project 的持久 Workdir
```

Conversation 不直接拥有目录，而是绑定 Project；Project 再绑定 UserWorkspace 下的 `workdir_path`。这样 Viewer、附件、artifact 和 Agent runtime 能指向同一份持久文件，但各入口仍使用自己的路径边界。文件访问通过 no-follow、拒绝 `..`、符号链接和特殊文件的原语完成。

需要特别注意：同一用户的整个 UserWorkspace 对 Agent runtime 可见，Project Workdir 是默认工作目录和产品范围，不是同一用户不同 Project 之间的强安全隔离边界。父 Agent 和子 Agent 共用 runtime/workdir，子 Agent 只隔离 checkpoint。

#### 2.3 Yuxi Memory 的读取、写入和历史检索

Yuxi 的 Memory 有三条链路：

1. **Prompt 读取**：读取用户配置 `enable_memory`；开关关闭、文件不存在或文件为空时不注入。开启后读取 `/agents/MEMORY.md` 的有界前缀，内容放进明确标记的 `<memory_data>`，并声明它是低信任参考资料而不是 system instruction。
2. **显式写入**：只有用户明确说“记住”时才允许 `remember_memory`。追加时做重复检测；纠正时要求 `replaces` 在现有文件中唯一匹配；更新后的文件有大小和 UTF-8 校验，并通过临时文件 + 原子替换发布。
3. **历史按需检索**：`search_thread_messages` 先搜索当前用户自己的普通主 Agent 历史，`read_thread_messages` 再读取有界消息；默认排除 tool 详情、子 Agent 线程、Agent 调用/评估来源和已删除会话。历史不是每轮自动塞进 prompt，而是先定位再读取。

写入前还会取得 `user-memory:<uid>` 的事务 advisory lock，并验证当前 `AgentRun` 的 `uid`、thread、request、worker lease 和顶层 Run 类型都一致。也就是说，Memory 工具不能只凭一个 `uid` 参数改文件；它必须属于当前仍由本 worker 持有的有效运行。

Yuxi 当前实现没有向量 Memory Store：固定记忆是 Markdown 文件，历史检索是 PostgreSQL 中有界的文本查询。这是一个适合第一阶段的简单取舍，先解决边界、可解释性和删除能力，再决定是否引入 embedding。

#### 2.4 Namespace 不是租户边界

Yuxi 代码中的 `namespace` 需要和业务租户概念分开看：

- `BaseAgent._stream_input_with_state` 从 `astream_events(version="v3")` 的 `params.namespace` 读取事件命名空间，并据此把主 Agent、子 Agent 的消息和工具事件路由到正确的前端展示节点。它是 LangGraph 执行树/子图的事件路由信息，不是用户、租户或 Project 的权限键。
- Agent 调用配置显式传入 `thread_id` 和 `uid`；`thread_id` 用于恢复 LangGraph 对话 state，`uid` 用于构造运行上下文和服务层身份校验。Yuxi 没有把 `tenant_id` 放进这个配置，也没有使用 `StoreBackend` 的 namespace 建立一套业务租户记忆树。
- LangGraph Checkpointer 内部可能存在自己的 checkpoint namespace，用于区分图或子图 checkpoint；这不能替代服务层的 `uid`、Conversation ownership 和 ACL。Yuxi 的精简 SSE 还会在输出前移除 `thread_id` 和 `namespace` 等内部字段。

因此不能把 `namespace`、`thread_id`、`uid` 和 Project 混成一个概念：namespace 解决“这条事件来自哪棵执行子图”，thread 解决“哪一段对话 state”，uid 解决“哪个用户”，Project 解决“哪份共享 Workdir”。

### 3. MelonClaw 当前实现与 Yuxi 的差异

MelonClaw 当前已经落地 Project/Conversation/Workdir 关系：`tenants`、`users`、`user_tenants`、`projects` 和 `chat_conversations` 组成用户及其租户标签；`user_tenants` 使用独立代理主键并允许一个用户属于多个租户。Project 只属于 User，一个 User 可以拥有多个 Project；Conversation 通过 `(project_id, user_id)` 校验归属，一个 Project 下的多个 Conversation 复用同一个 Project workdir。Tenant 只用于标记/选择用户，不参与 Project 或 Conversation ownership。Web service 会从数据库重新解析用户租户标签；运行时通过 `AgentContext` 把用户、当前标签和 Project 带入 Agent。

它与 Yuxi 的关键差异是：

- **租户模型**：MelonClaw 使用独立 `tenants` 和 `user_tenants` 关系；一个用户可以加入多个租户，`user_tenants.id` 是代理主键，`(user_id, tenant_id)` 仅保持关系唯一。Yuxi 没有在当前业务表中设置一等 `tenant_id`，但它有真实认证、角色和资源共享 ACL。MelonClaw 的模拟用户下拉框只是开发入口，不能视为生产多租户认证。
- **Project ownership**：MelonClaw 的 Project 只绑定 `user_id`，Conversation 只绑定 `user_id + project_id`；tenant 只是用户可选择的标签，不是 Project 或 Conversation 的分区键。Yuxi 的 Project/Conversation 核心绑定是 `uid`，组织主要用于 Agent、Skill、知识库等资源的共享范围匹配，也不是 Project 的共享成员模型。
- **默认 Project**：MelonClaw 为每个用户维护一个可见的“临时会话”，旧会话和未指定 Project 的新会话都归并进去；Yuxi 手动创建的 selectable Project 可以承载多个 Conversation，但未指定 Project 时会为该 Conversation 创建一个隐藏的 implicit Project，并非用户唯一默认 Project。
- **文件物理边界**：MelonClaw 使用全局 `MELONCLAW_WORKSPACE_DIR/projects/<project_uuid>`，依赖数据库中的用户和 Project 校验；Yuxi 使用 `shared/<safe_uid>/workspace/projects/<uuid>` 的 UserWorkspace，再由 Workdir 和 no-follow 文件原语限制访问。MelonClaw 当前的 `LocalShellBackend` 仍是本机开发后端，不能作为生产隔离边界。
- **Agent/Checkpoint**：MelonClaw 的 `AgentContext` 包含用户、租户、Project 和 Workdir，Checkpointer 配置主要使用 `conversation_id` 作为 `thread_id`；当前没有 Yuxi 那样的事件 `namespace` 路由处理，也没有把 namespace 当作权限边界。两者都应把 thread state 和长期 Memory 分开。
- **长期 Memory**：Yuxi 有用户级 `agents/MEMORY.md`、显式 `remember_memory`、用户历史检索和写入锁；MelonClaw 已在本次迭代增加 PostgreSQL Store-backed 的 Global/Tenant/User Memory middleware、固定工具、审计事件和跨 thread 记忆。

因此，MelonClaw 当前更准确的描述是“带用户标签、Project ownership 校验和三类长期 Memory 的单部署多用户演示”，而不是完整的企业多租户系统。`tenant_id` 是请求运行上下文和 Tenant Memory 的共享范围键，不是 Conversation 的归属键，仍不能单独替代 `user_tenants` 成员校验。`namespace` 仍只作为 Store/事件/执行树的技术分层，不承担业务授权职责。

### 4. 建议的 MelonClaw 长期 Memory 目标模型

#### 4.1 身份上下文

每次请求先由认证层生成不可伪造的 `RequestContext`，至少包含：

```text
user_id
tenant_id（当前请求选择的租户，需校验属于该 user）
project_id
thread_id
run_id / request_id
worker_id / lease
memory_permissions（user read/write、tenant read/write）
```

当前应用已具备 `tenants` 和 `user_tenants` 基础关系：一个用户可以有多个租户标签，`user_tenants.id` 是代理主键，`(user_id, tenant_id)` 只保持关系唯一。Conversation 不绑定 `tenant_id`；创建、发送消息、恢复审批和读取历史时，服务层只校验当前请求的用户租户成员关系，并按该运行上下文选择 Tenant Memory。这样同一个用户可以在不同租户中使用同一个 Project 和 Conversation，用户 Memory 仍按 `user_id` 归属并跨该用户的租户上下文共享。

#### 4.2 Workspace 目录边界

建议提供两个逻辑文件根，底层可以是本地 no-follow 文件系统、对象存储或受控 Sandbox 挂载：

```text
users/<user_id>/workspace/
users/<user_id>/projects/<project_id>/workspace/
```

Agent 看到的是经过 resolver 映射的虚拟路径，例如 `/user-data/` 和 `/project/`，而不是宿主绝对路径。`/skills/` 继续只读。用户 Workspace 只允许用户本人和受控系统流程写入；不能把多个用户 Workspace 通过一个宽泛的根目录交给 `LocalShellBackend`。如果未来增加租户共享文件，其 Workspace 应另建租户成员 ACL，不应把 tenant Memory 命名空间当成文件系统授权。

Workspace 文件是资料和可编辑上下文，不等于 Memory。Project Workdir 中的文件不会因为存在就自动进入每轮 prompt；Agent 需要通过显式引用、搜索或工具读取。

#### 4.3 Memory 的事实模型

建议以 LangGraph PostgreSQL Store 中的 JSON 文档作为 Memory 内容事实源，逻辑上统一为 `memory_items`，核心字段如下：

```text
id
scope_type: user | tenant
scope_id
agent_id
key / category / content
status: active | deleted | expired
source: user_explicit | agent_proposed | imported
created_by, updated_by
version, expires_at, created_at, updated_at
```

同时保留轻量 `memory_events` 审计表，记录创建、修改、删除、发布、撤销、操作者、run/request、前后版本和内容 hash。它是审计事实，不是第二份内容源。第一期不新增独立 `memory_items` 业务表，直接利用 Store 的 namespace/key/value；如果以后需要复杂排序、全文检索或跨 Store 统计，再评估把内容迁移到专用表。租户 Memory 应按主题或 key 拆分文档，避免所有成员并发修改一个大 Markdown 文件。

推荐的 Store 命名空间是：

```text
("melonclaw", "memory", "user",   agent_id, user_id)
("melonclaw", "memory", "tenant", agent_id, tenant_id)
```

所有 Store 读写都由服务端从已验证的运行上下文计算 namespace；工具参数不能传入任意 `user_id`、`tenant_id` 或 namespace。用户 Memory 的 key 应在用户命名空间内唯一，租户 Memory 的 key 应在租户命名空间内唯一；租户成员关系只负责授权，不改变已存在的用户 Memory。

### 5. DeepAgents 集成方式

保留当前 PostgreSQL Checkpointer 作为短期 thread state；长期 Memory 使用同一 PostgreSQL 上的 `AsyncPostgresStore`，不能使用 `InMemoryStore`。如果通过 DeepAgents 文件能力暴露长期存储，用两个 Store 路由区分个人和租户：

```text
/summary.md、临时研究文件  -> StateBackend，按 thread 丢弃
/memories/user/**          -> StoreBackend，namespace=(agent_id, user_id)
/memories/tenant/**        -> StoreBackend，namespace=(agent_id, tenant_id)
/user-data/、/project/      -> 经过 user/project resolver 的 Workspace backend
/skills/                   -> 只读项目 Skill backend
```

对应的装配形状是：

```python
backend = CompositeBackend(
    default=runtime_backend,
    routes={
        "/memories/user/": StoreBackend(
            namespace=lambda rt: (
                "melonclaw", "memory", "user",
                "quickstart-research-agent", rt.context.user_id,
            ),
        ),
        "/memories/tenant/": StoreBackend(
            namespace=lambda rt: (
                "melonclaw", "memory", "tenant",
                "quickstart-research-agent", rt.context.tenant_id,
            ),
        ),
    },
)

agent = create_deep_agent(backend=backend, store=postgres_store, ...)
```

DeepAgents 0.7 要求传入已构造的 backend，并要求 StoreBackend 显式 namespace；`postgres_store.setup()` 应由 `melonclaw-db-init` 执行，Web/CLI 只打开和校验。这里的 `memory=` + 官方 `MemoryMiddleware` 可以作为文件记忆入口，但它默认允许通用 `edit_file` 更新，并会把已加载内容放进当前 thread state。生产方案应使用自定义 `MemoryScopeMiddleware`：按已验证的 user/tenant scope 有界加载 prompt，并注册固定参数的专用工具；同时用权限规则禁止通用文件工具写 `/memories/**`，避免绕过审计和租户写入策略。

在 Web 服务里不要把宿主 `FilesystemBackend` 当作授权边界；文件和 Memory 工具必须通过受控服务、Sandbox 或专门 backend 访问。

建议增加一个运行时 Memory middleware，职责是：

- 从 `runtime.context` 读取 user/tenant/project/run 身份，每次请求动态计算可见 scope；不在全局 Agent 单例中缓存某个用户或租户的 prompt。
- 仅注入有界的“核心记忆摘要”：个人 Memory 和租户 Memory 分开标记，并明确都是不可信参考资料。
- 暴露受限的异步工具：`search_memory`、`read_memory`、`remember_user_memory`、`propose_tenant_memory`、`forget_memory`。工具不接受任意身份或路径作为可信授权输入，目标由服务器上下文和 ACL 决定。
- 只有当前用户明确要求长期保存时才写入个人 Memory；租户 Memory 第一版由租户管理员/API 写入，Agent 只能提出变更。若开放 Agent 写入，必须同时满足成员写权限、明确请求、HITL 和幂等审计。
- 历史检索默认只读，不触发写入；它属于 episodic history，不自动升级为个人或租户 Memory。

当前 `AgentContext` 已包含用户、租户标签和 Project 展示字段；长期 Memory 版本应补充上面的 scope 权限快照，并给每次执行一个服务端生成的 `run_id`。当前 Web 按 Project 缓存 Agent 是可保留的，StoreBackend 的 namespace factory 会在调用时依据 Runtime 计算。Conversation 不绑定 tenant，Memory middleware 每次模型调用都按当前运行上下文重新计算 namespace。

### 6. 一次请求的推荐链路

```text
认证上下文
  -> 校验 user
  -> 校验 tenant 成员关系和 memory ACL
  -> 校验 conversation 与 user/project 归属
  -> 生成 run/request identity
  -> 按 ACL 读取 user + tenant 核心 Memory
  -> 以不可信 <memory_data scope=user/tenant> 注入主 Agent
  -> 按需 search/read 历史或 Workspace
  -> 明确请求后调用个人 remember/forget；租户变更先形成 proposal
  -> scope 锁 + version/CAS + 审计 + 幂等提交
  -> 最后提交业务终态，再发送 SSE completed
```

固定记忆的第一版可以采用 Yuxi 的简单策略：追加去重、精确替换、单条和总量上限、UTF-8 校验、截断标记。Store 没有配置 embedding 时，Memory 检索先按固定 key/category 和有界读取实现；历史记忆继续优先使用现有 PostgreSQL 全文/关键词检索。只有数据规模和效果证明需要时，再引入 pgvector，并且向量查询必须先带 user/tenant namespace 过滤，不能先全库召回后再猜权限。

### 7. 权限和安全取舍

建议的最低权限如下：

| Scope | 读取 | 写入/删除 |
| --- | --- | --- |
| user | 当前用户 | 本人明确请求；系统按当前 Run 写入 |
| tenant | 当前租户的 active 成员 | 第一版由租户管理员/API；Agent proposal 必须经 ACL + HITL |
| episodic history | 本人历史；跨用户历史必须另有 ACL | 由聊天业务流产生，不允许 Memory 工具改写 |

“global”若保留，应解释为“当前部署内、经过明确 ACL 的全局资源”，不能把 tenant 标签或 Store namespace 当成授权替代。跨用户读取只能由平台级治理操作完成，并留下独立审计记录。

所有写操作还应具备以下约束：

- 事务锁定 Memory scope/key，而不是只锁 conversation；租户共享 Memory 使用 advisory lock 加 version/CAS，必要时按主题拆分 key。
- 验证 run 仍属于同一 user、project、thread、request，且执行 owner/lease 有效；旧 worker 不能在恢复后继续写 Memory。
- 使用 operation/request 幂等键，重复工具调用不重复追加；删除保留 tombstone，避免旧重试把记忆复活。
- Memory 内容按数据处理，不能覆盖当前 system/user 指令；禁止凭据、Token、临时推测和只属于当前 Project 的私密事实进入个人核心记忆，也禁止普通成员把未审核指令写进租户 Memory。
- 用户切换 tenant 标签时，个人 Memory 仍保持不变；同一个用户可以继续使用已有 Conversation，但本轮 Memory namespace 按新的租户运行上下文重新计算。

### 8. 长期 Memory 的实施路线

1. **第一阶段：身份与 Store 基础**。补齐成员状态/角色，初始化 `AsyncPostgresStore`，实现 user/tenant namespace 和 scope fingerprint；Conversation 仍只按 User + Project 归属。
2. **第二阶段：个人 Memory**。沿用 Yuxi 的显式记住、大小预算、历史按需检索和当前 Run 校验，按 `user_id` 建立长期记忆。
3. **第三阶段：租户 Memory 只读共享**。租户管理员/API 发布共享事实；普通 Agent 只能读取，通用文件工具禁止写入，增加 Memory 管理和审计接口。
4. **第四阶段：租户 Memory 提案写入**。加入 `propose_tenant_memory`、成员 ACL、HITL、version/CAS、幂等和冲突反馈。

5. **第五阶段：检索增强**。用真实查询日志评估 key/关键词检索，再决定是否引入 embedding、过期策略、后台整理和 Memory 管理页面。

验收重点不是“模型说它记住了”，而是重新读取 PostgreSQL Store、审计事件和 Checkpointer 状态，验证以下负向案例：错误 user_id 读不到个人 Memory；非成员读不到租户 Memory；tenant A 的成员不能读写 tenant B；同一用户在不同租户上下文中只能读到对应 Tenant Memory；未明确要求时不会写；普通成员不能直接写租户 Memory；旧 run/错误 user_id 不能写；同一 request 重试不会重复写；删除后旧数据不会被恢复。

### 9. 根据新增反馈优化：全局 Memory 与 Docker Sandbox 解耦

#### 9.1 Yuxi 的记忆到底存在哪里

结论：Yuxi 的固定长期记忆是文件，不是 LangGraph `StoreBackend`。

- 固定记忆文件是每个用户 Workspace 下的 `/agents/MEMORY.md`，由 `Workspace(uid)` 读取和原子替换；实现见 Yuxi 的 `backend/package/yuxi/services/memory_service.py`。
- PostgreSQL 在这条链路中负责 `enable_memory` 配置、AgentRun/worker lease 身份校验、事务 advisory lock，以及历史消息的搜索和读取。
- Yuxi 当前代码没有 `PostgresStore`、`BaseStore` 或 `StoreBackend` 的记忆实现；它把“文件作为 Memory 事实源”和“数据库负责控制面/历史数据”分开了。

因此，MelonClaw 不必复制 Yuxi 的文件物理实现，但应保留它的行为语义：显式记忆、低信任注入、大小预算、精确替换、并发保护、删除能力和运行身份校验。MelonClaw 选择 PostgreSQL Store 是持久化介质的升级，不是对 Yuxi 记忆边界的否定。

#### 9.2 四种记忆作用域

在原有 user/tenant 之外增加 global，但“global”必须定义为当前部署/Agent 的共享作用域，而不是任意用户都可以写入的超级权限空间：

| Scope | 内容 | 读取 | 写入 |
| --- | --- | --- | --- |
| global | 产品级事实、审核通过的通用知识、Agent 公共工作规范 | 所有通过认证的 Agent Run | 平台管理员、开发发布流程或审核后的后台任务 |
| tenant | 租户术语、流程、共享约束、租户批准的事实 | 当前租户 active 成员 | 租户管理员/API；Agent 默认只能提案 |
| user | 用户偏好、稳定背景、个人工作习惯 | 当前用户，跨其租户复用 | 当前用户明确请求 |
| episodic | 历史对话、项目过程、过去的执行结果 | 按用户/Project/ACL 检索 | 聊天业务流产生，Memory 工具不可改写 |

Global Memory 不能承载真正的 system/developer policy。安全策略、工具权限和不可覆盖的行为约束仍应位于代码、配置或 system prompt；Global Memory 只提供经过审核的参考资料，并以低信任内容注入。否则任何一次全局内容污染都会影响所有租户。

推荐 namespace：

```text
("melonclaw", "memory", "global", installation_id, agent_id)
("melonclaw", "memory", "tenant", installation_id, agent_id, tenant_id)
("melonclaw", "memory", "user",   installation_id, agent_id, user_id)
```

`installation_id` 用于同一 PostgreSQL 实例承载多个部署时避免互相污染；`agent_id` 用于同一部署中隔离研究 Agent、编码 Agent 等不同 Agent。Global Memory 不建议使用一个没有部署和 Agent 维度的单一 namespace。

#### 9.3 Postgres Store 是否会限制未来 Docker Sandbox

不会，前提是现在就明确两者的职责：

```text
Postgres Checkpointer  -> thread state、HITL 恢复、短期对话状态
Postgres Store         -> global/user/tenant 长期 Memory 的事实源
Docker Sandbox         -> shell、代码执行、临时文件、Project 工作环境
```

DeepAgents 的 `CompositeBackend` 本来就支持“默认使用 Sandbox，同时把某些路径路由到 StoreBackend”；官方示例也展示了把 `/memories/` 路由到 Store、把默认 backend 换成远端 Sandbox 的组合方式。[Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes) [Backends](https://docs.langchain.com/oss/python/deepagents/backends)

未来切换时，理想变化只有这一处：

```text
当前：default = LocalShellBackend(Project Workdir)
未来：default = DockerSandboxBackend(thread/project sandbox)
两者共同保留：MemoryService + PostgreSQL Store + Memory Middleware
```

需要遵守五条迁移原则：

1. Sandbox 不是 Memory 的事实源。容器可以按 thread 或 Project 创建、复用、过期和销毁；Memory 必须跨容器生命周期保留在 PostgreSQL Store。
2. 不要把 PostgreSQL 凭据或 Store 连接直接放进 Sandbox 环境变量，也不要让 Sandbox Shell 直接访问数据库。由应用服务通过受控工具或 middleware 访问 Memory。
3. 不要用 sandbox ID 推导 Memory namespace。Sandbox 是执行环境标识，Memory namespace 必须由已认证的 `user_id`、已校验的 `tenant_id`、固定的 `installation_id/agent_id` 计算。
4. 如果确实需要让 Sandbox 内的程序读取记忆，只能由应用把有界、脱敏、只读的 Memory 快照上传到容器；程序产生的修改通过提案接口回到应用服务，不允许容器直接覆盖 Store。
5. Project Workdir 的 Sandbox 生命周期独立于长期 Memory。若 Project 需要跨 Conversation 共享文件，可保存 Sandbox 映射或使用持久卷；这不改变 Memory 的授权模型。

因此，第一期不应把 Memory 文件挂载进 Docker。更稳妥的实现是：Agent 主图通过 `MemoryScopeMiddleware` 读取 global/user/tenant 的有界摘要，固定工具调用 MemoryService；CompositeBackend 只负责临时文件、Project 工作区和只读 Skills。未来换 Docker 时，只替换 CompositeBackend 的 default 路由和 Sandbox 生命周期管理。

#### 9.4 优化后的最终组件关系

```text
认证与租户成员校验
        |
        v
不可伪造 RequestContext
(installation, agent, user, tenant, project, thread, run, lease)
        |
        +--> MemoryAuthorization
        |       |- global: read only
        |       |- tenant: active member read / admin or proposal write
        |       `- user: current user explicit write
        |
        +--> MemoryService
        |       |- AsyncPostgresStore: 内容事实源
        |       |- memory_events: 审计与版本事件
        |       `- lock/CAS/idempotency: 并发和重试控制
        |
        +--> MemoryScopeMiddleware
        |       `- 注入低信任、有界的 global/user/tenant 内容
        |
        `--> CompositeBackend
                |- default: 当前 LocalShell，未来 Docker Sandbox
                |- /project/**: Project 工作环境
                |- /skills/**: 只读 Skill
                `- /memories/**: 如需文件接口，仅作为受限只读投影
```

这里的 `/memories/**` 不应成为生产写入的主要入口。DeepAgents 内置文件记忆默认允许 Agent 通过 `edit_file` 更新；对于 tenant/global 这种共享作用域，应采用只读路由或自定义 backend policy，再由专用工具走 MemoryService。官方文档也建议共享组织记忆默认只读，并通过应用代码、Store API 或人工审批更新。[Memory](https://docs.langchain.com/oss/python/deepagents/memory)

#### 9.5 重新排序后的实施阶段

1. **控制面抽象**：先定义 `MemoryService`、四种 scope、namespace 规则、审计事件和权限决策；Conversation 只保留 User + Project 归属。
2. **Global Memory 只读**：由后台/API 导入审核后的全局内容，Agent 只能读取，验证所有用户都能读但普通用户不能写。
3. **Personal Memory**：实现显式 remember/forget、跨租户复用、用户级锁和幂等。
4. **Tenant Memory 只读**：active 成员可读，管理员/API 发布；测试租户隔离和用户跨租户个人记忆不变。
5. **Tenant 提案写入**：加入 ACL、HITL、version/CAS、审计、冲突反馈和删除 tombstone。
6. **Docker Sandbox 替换**：保持 MemoryService、Store namespace、middleware 和权限测试不变，只替换默认执行 backend、sandbox 生命周期和 Project 文件同步。

这样即使未来 Docker Sandbox 供应商、容器生命周期或 Project 文件持久化策略发生变化，global/user/tenant Memory 的数据模型、权限和 API 都不需要重做。
### 10. 已实现：Project 共享工作目录与长期 Memory

当前已落地 Yuxi 的 Project/Conversation/Workdir 关系，以及 Global/Tenant/User 长期 Memory 的第一版控制面与 Store 接入：

```text
用户
  -> Project（projects.id，持久 workdir_path）
  -> 多个 Conversation（chat_conversations.project_id）
  -> 每个 Conversation 独立 thread_id/checkpoint
  -> 同一 Project 的 Agent backend 共享同一个宿主工作目录
```

具体行为：

- 新增 `projects` 表：保存 Project 名称、用户归属、`workdir_path`、状态和时间戳；Project 工作目录默认是 `projects/<project_uuid>`。
- `chat_conversations.project_id` 为必填，并用 `(project_id, user_id)` 联合外键保证会话不能挂到其他用户的 Project；同一个 Project 下的多个 Conversation 共享该 `workdir_path`。
- 新增 `POST /api/projects`、`GET /api/projects`；创建会话时可传 `project_id`。不传时自动使用当前用户唯一的“临时会话” Project，因此第一次聊天不需要先手动创建 Project。
- Web 左侧在模拟用户下展示“项目”文件夹列表和“新增项目”入口；点击一个文件夹后查看其中的聊天会话，在同一个 Project 下连续新建多个对话，它们会进入不同的会话列表项，但 Agent 文件操作命中同一个共享目录。
- Web 服务按 Project 缓存 Agent backend：同一 Project 复用同一个 `LocalShellBackend(root_dir=...)`，不同 Project 使用不同根目录；每个会话仍使用自己的 `conversation_id` 作为 PostgreSQL Checkpointer 的 `thread_id`，不会共享对话上下文、Todo 或 HITL 状态。
- `MELONCLAW_WORKSPACE_DIR` 可指定持久 workspace 根目录；未配置时使用 `~/.melonclaw/workspaces`。它与原来的 `temp/melonclaw-*` 运行临时目录分开，Project 文件不会随进程退出清理。
- 数据库升级是幂等的。每个已有用户都会自动拥有一个名为“临时会话”的默认 Project；Project 功能上线前产生的旧会话会全部归并到该用户的默认 Project，不会删除旧消息或 checkpoint。上一版迁移生成的“历史会话”项目会标记为 deleted，原有文件字节不做破坏性删除。

当前实现边界：

- Project 仍属于当前用户；Conversation 只通过 `user_id + project_id` 校验归属，不保存或绑定 `tenant_id`。`tenant_id` 仍会随请求传入，用于成员校验和选择当前 Tenant Memory。
- `AsyncPostgresStore` 保存 Global/Tenant/User Memory 内容；`memory_events` 保存发布、提案、记住和删除事件，Store 内容仍是唯一事实源。
- 主 Agent 每次模型调用按已验证的 `AgentContext` 动态加载三类有界、低信任 Memory，并提供 `search_memory`、`read_memory`、`remember_user_memory`、`forget_user_memory` 和 `propose_tenant_memory` 固定工具；工具 schema 不接受 user/tenant/namespace 身份参数。
- 个人 Memory 已支持显式追加去重、精确替换、删除、大小限制、运行身份校验、scope/key advisory lock 和 request 幂等；租户 Memory 已支持成员读取与普通成员提案，管理员发布入口已留在 `MemoryService.publish_curated`，尚未开放无认证的后台 API。
- Global Memory 已支持受控读取和 `memory_admin` 发布路径，但当前演示身份没有登录鉴权和平台管理员界面；Global 内容不能替代 system/developer policy。
- 当前 Web 仍使用模拟用户/租户身份，后端会做成员和资源归属校验；`LocalShellBackend` 仍只适合本机受信任环境。未来替换 Docker Sandbox 时只需替换 CompositeBackend 的 default 执行后端，MemoryService、PostgreSQL Store、namespace 和审计模型保持不变。
- `build_agent_backend` 已预留 `default_backend` 注入点，`build_research_agent` 以 `runtime_backend` 透传；未来 Docker Sandbox 只替换这个执行后端，不需要改 MemoryService、Memory 工具或 Store namespace。
