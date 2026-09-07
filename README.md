# DeepAgents 学习项目

这是一个持续演进的 Deep Agents 学习项目。当前累计应用是官方 [Deep Agents Python Quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart) 的研究 Agent：它使用 Tavily 自定义搜索工具，结合 Deep Agents 的文件工具、子 Agent、自定义 callable 工具、MCP 工具和 [Interpreters](https://docs.langchain.com/oss/python/deepagents/interpreters) 生成研究报告；配置 Tushare token 后会自动注入金融数据工具。应用同时提供 CLI 和浏览器 Web 交互入口。

## 当前入口

- 学习笔记与设计取舍：[quickstart_research.md](quickstart_research.md)
- 核心配置：[config.py](src/melonclaw/core/config.py)
- MCP 配置与脱敏：[mcp_config.py](src/melonclaw/core/mcp_config.py)
- 系统提示词：[prompts.py](src/melonclaw/core/prompts.py)
- Agent Skill：[research-workflow/SKILL.md](skills/research-workflow/SKILL.md)
- Skill 后端路由：[skills.py](src/melonclaw/core/skills.py)
- 自定义中间件：[middleware](src/melonclaw/middleware/)
- 人工审批与恢复：[hitl.py](src/melonclaw/core/hitl.py)
- QuickJS Interpreter 配置：[interpreter.py](src/melonclaw/core/interpreter.py)
- 输出内容处理：[content.py](src/melonclaw/output/content.py)
- 流式交互输出：[streaming.py](src/melonclaw/output/streaming.py)
- 流式事件适配：[events.py](src/melonclaw/output/events.py)
- 文件调用排序：[file_ordering.py](src/melonclaw/middleware/file_ordering.py)
- 动态工具选择：[tool_selection.py](src/melonclaw/middleware/tool_selection.py)
- Tavily 搜索：[search.py](src/melonclaw/tool/search.py)
- Agent 组装：[agent.py](src/melonclaw/core/agent.py)
- 工具装配与 MCP：[tools.py](src/melonclaw/tool/tools.py)
- CLI：[main.py](src/melonclaw/main.py)
- Web 入口：[main_web.py](src/melonclaw/main_web.py)（FastAPI + Uvicorn）
- Web 服务、模拟用户、租户隔离、Project 与聊天持久化：[web](src/melonclaw/web/)
- MCP Server/Client 学习示例：[example/mcp](example/mcp/)

## 运行

项目默认读取根目录 `.env` 中的 `DEEPSEEK_BASE_URL`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`TAVILY_API_KEY` 和 `DATABASE_URL`；不会打印任何 API Key。当前本机 PostgreSQL 配置为 `postgresql+asyncpg://yingshuowang@127.0.0.1:5432/melonclaw`：业务查询使用 asyncpg，LangGraph Checkpointer 启动时从同一 URL 派生 psycopg URL。Agent 构建时还会把应用启动日期（运行机器本地时区）写入系统提示词，用于理解“今天”“昨天”等相对日期。CLI 只创建一个 asyncio 事件循环，Agent 构建、MCP 工具发现和多轮对话都在该循环内异步执行。

Agent 还启用了项目 Skill 目录。启动时只把 Skill 的 `name` 和 `description` 放入系统提示词；当问题匹配 `research-workflow` 时，Deep Agents 再通过 `read_file` 读取完整的 `SKILL.md`。虚拟 `/skills/` 路由直接读取仓库根目录下与 `src/` 平级的 `skills/`，不复制到临时 runtime；CLI 的研究中间文件写入临时 runtime，Web 的文件操作则写入当前 Project 的持久工作目录。CLI 使用临时后端，Web 按 Project 使用共享后端，文件操作与 Shell 执行都经过 HITL 审批。

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw
```

`melonclaw-db-init` 只需在首次部署或升级 Checkpointer 表结构时执行一次；CLI 和 Web 都使用 PostgreSQL Checkpointer，未初始化时会明确报错，不会回退到内存保存。

启动后在 `🧑>` 提示符输入问题；当前交互模式会持续显示 AI 文本、工具调用和工具结果：

```text
🧑> 请研究 LangGraph 的 StateGraph 和节点模型。
```

### Web 交互入口

浏览器交互使用与 CLI 相同的累计 Agent、模型配置和 MCP 配置；后端优先通过 Deep Agents/LangGraph `astream_events(version="v3")` 并发消费协调 Agent 与 `general-purpose` 子 Agent，原生 SSE 把文本、工具调用、子 Agent 生命周期和工具结果实时推送到页面，敏感的文件操作或 Shell 操作会在页面中等待人工审批。前端保留现有 FastAPI/SSE + 原生 `fetch` reader，不引入 React 或 LangChain 前端 SDK。Web 模式使用 PostgreSQL 中的模拟用户和租户关系；一个用户可以属于多个租户，左侧下拉框展示“用户中文名-租户中文名”，其下方以文件夹列表展示 Project，并提供“新增项目”入口；点击文件夹后进入该 Project 的聊天会话列表。多个会话可以归属同一个 Project，共享 Project 工作目录；每个 `conversation_id` 仍作为已校验的 LangGraph `thread_id`，由 PostgreSQL Checkpointer 保存独立 Agent 上下文。

```bash
uv sync
uv run melonclaw-db-init
uv run melonclaw-web
# 等价入口：uv run python -m melonclaw.main_web
```

`melonclaw-db-init` 会幂等创建或升级 `tenants`、`users`、`user_tenants`、`projects`、`chat_conversations`、`chat_messages` 和 Checkpointer 表，并写入演示租户和用户；`user_tenants` 使用独立关系 ID，不把 `user_id + tenant_id` 作为主键，并允许一个用户加入多个租户。每个用户都会自动拥有一个名为“临时默认”的默认 Project，Project 功能上线前的旧会话会归并到该用户的默认 Project，不删除或重建已有数据。Tenant 只是用户标签，不会把同一个用户的 Project 或 Conversation 切分成不同集合。Project 工作目录根目录可通过 `MELONCLAW_WORKSPACE_DIR` 配置，默认是 `~/.melonclaw/workspaces`，不会随进程退出清理。API 启动时只检查表是否存在；如果数据库不可用或尚未初始化，`/api/status` 会明确报告错误。当前应用运行在宿主机，因此 `127.0.0.1` 指本机 PostgreSQL；若以后放进容器，需要将 `DATABASE_URL` 的主机改为数据库服务名或宿主机可达地址，不能继续照搬容器内的 `127.0.0.1`。

然后打开 <http://127.0.0.1:8000>。如果需要局域网访问或更换端口，可在启动前设置：

FastAPI 自动生成的接口文档位于 <http://127.0.0.1:8000/docs>，可用于查看请求体模型和调试 API。

```bash
MELONCLAW_HOST=0.0.0.0 MELONCLAW_PORT=8080 uv run melonclaw-web
```

页面启动后会先显示 Agent 和数据库状态；就绪后可切换模拟用户、分页浏览会话、新建会话、加载历史并继续聊天。每轮消息都会逐步显示文本和工具过程；触发 HITL 时，审批卡片支持批准、编辑 JSON 参数或拒绝并附带原因。`main.py` 和 `uv run melonclaw` 仍保留为 CLI 学习入口。

默认监听 `127.0.0.1` 且没有登录鉴权，适合本机学习；如果绑定到局域网或公网，请先在反向代理或应用层增加鉴权与 HTTPS。

启动日志中应看到：

```text
已启用 Agent Skill: /skills/
```

### 聊天持久化与接口

Web API 的开发身份和核心接口如下。`/api/dev/users` 从数据库读取用户、中文名和租户标签；浏览器可提交 `user_id + tenant_id` 选择标签，后端会重新校验用户是否属于该租户，但 Project 和 Conversation 仍按用户归属：

```text
GET  /api/dev/users
POST /api/projects                              {"user_id":"zhangsan","name":"研究项目","tenant_id":"research"}
GET  /api/projects?user_id=zhangsan&tenant_id=research
POST /api/conversations                         {"user_id":"zhangsan","project_id":"...","tenant_id":"research"}
GET  /api/conversations?user_id=zhangsan&tenant_id=research&project_id=...&limit=20&cursor=...
GET  /api/conversations/{id}/messages?user_id=zhangsan&tenant_id=research&limit=50
POST /api/conversations/{id}/messages            {"user_id":"...","tenant_id":"research","request_id":"UUID","content":"..."}
POST /api/conversations/{id}/approval            {"user_id":"...","tenant_id":"research","decisions":[...]}
```

`tenants` 保存租户标签 ID 和中文名，`users` 保存用户 ID 和中文名，`user_tenants` 保存用户与租户的多对多关系；`projects` 只保存用户归属和持久 `workdir_path`；`chat_conversations` 保存 `user_id` 和必填 `project_id`，并用 `(project_id, user_id)` 联合外键保证 Conversation 不能挂到其他用户的 Project。Tenant 不参与 Project 或 Conversation ownership。多个会话可以指向同一个 Project，但每个会话仍有独立 Checkpointer thread。`chat_messages` 只保存用户消息和展示用的助手消息；Checkpointer 的内部表不参与会话列表查询。发送消息时先以 PostgreSQL advisory try-lock 保证同一会话单执行，再以短事务保存用户消息和助手占位记录；流结束后才提交助手最终正文。重复 `request_id` 会重放已保存结果，正文不一致返回 409；不存在或不属于当前用户或 Project 的会话统一返回 404。前端切换用户、租户标签、Project 或会话时会取消旧流，并用 user/conversation/代次检查丢弃迟到事件。

默认会注入 Tavily 的 `internet_search` 自定义工具。Tavily 搜索需要在 `.env` 中配置：

```bash
TAVILY_API_KEY=你的 Tavily API Key
```

MCP 服务定义统一写在根目录 `mcp.json` 中，不支持把 MCP 服务 JSON 写入 `.env`。文件使用较常见的 `mcpServers` + `type` 格式，便于复制到兼容该格式的客户端；项目加载时会把它规范化为 `langchain-mcp-adapters` 的 `transport` 格式，同时继续兼容旧的 `servers` + `transport`。如果暂时不用 MCP，可以把文件内容改为 `{}`，或将整段内容用 `//` 注释掉；这两种情况都会正常启动且不建立 MCP 连接。依赖安装包含 MCP 适配器：

```bash
uv sync
```

### Tushare MCP 接入累计 Agent

真实凭据只放在已被 Git 忽略的根目录 `.env`：

```dotenv
TUSHARE_MCP_TOKEN=你的本地凭据
```

仓库中的 `mcp.json` 只保存 `${TUSHARE_MCP_TOKEN}` 占位符，并使用 Tushare 当前文档中的 `/mcp/token=...` URL 形式。累计应用检测到 token 后，会自动从该文件选择 `tushare_mcp`，通过 `MultiServerMCPClient.get_tools()` 获取工具名称、描述和参数 schema，再把 Server 返回的全部工具注册到 `create_deep_agent(tools=...)`。当前远程发现得到 250 个 Tushare 工具，加上 `internet_search` 共 251 个外部工具。

项目不会把 251 份完整工具 schema 同时发送给主模型，而是每轮先从完整目录选出最多 16 个相关工具。选择器按 provider 切换：`DEEPAGENTS_PROVIDER=openai` 使用 LangChain 官方 `LLMToolSelectorMiddleware` 及 OpenAI 原生 structured output；默认的 `deepseek` 使用项目自定义 `CatalogToolSelectorMiddleware`，以普通 JSON 文本兼容 DeepSeek 当前不接受的 `response_format=json_schema`。两条路径都保留完整接口目录，只是按需把工具 schema 放入单次模型上下文。

运行 Agent：

```bash
uv run melonclaw
```

启动时应看到服务名和注入的工具名，但不会显示 URL 或 token；随后可以直接提问：

```text
已启用 MCP 服务: tushare_mcp
已注入 Agent 工具: 251 个（前 12 个：internet_search, stock_basic, trade_cal, ...）
每轮主模型动态选择工具上限: 16
动态工具选择器: 项目自定义 CatalogToolSelectorMiddleware

🧑> 请查询上交所 2026-01-05 是否开市，只使用 Tushare 数据。

🧭 [动态工具选择] trade_cal
```

切换到 OpenAI 时，启动日志会改为 `动态工具选择器: LangChain 官方 LLMToolSelectorMiddleware`：

```dotenv
OPENAI_API_KEY=你的 OpenAI API Key
OPENAI_MODEL=支持 structured output 的 OpenAI 模型名
```

```bash
DEEPAGENTS_PROVIDER=openai uv run melonclaw
```

如果要显式选择 `mcp.json` 中的服务，可以增加逗号分隔的服务名；显式设置为空可关闭自动启用。这个变量只选择 `mcp.json` 中已有的服务，不承载服务定义：

```dotenv
DEEPAGENTS_MCP_SERVER_NAMES=tushare_mcp
# DEEPAGENTS_MCP_SERVER_NAMES=
```

默认注册全部 Tushare 工具。如果以后想在动态选择前就缩小候选目录，可以用逗号分隔的工具名显式设置白名单；设置为空或 `*` 都表示恢复全部开放：

```dotenv
DEEPAGENTS_TUSHARE_MCP_TOOLS=stock_basic,trade_cal,adj_factor,daily,daily_basic
# DEEPAGENTS_TUSHARE_MCP_TOOLS=*
```

`example/mcp/` 的通用 Client 仍可绕过模型，独立发现和调用远程工具。先查看 Server 实际暴露的工具名称和描述：

```bash
uv run python example/mcp/client/mcp_client.py \
  --server tushare_mcp \
  --list-tools
```

再把发现到的工具名传给 `--tool`，并用 `--arguments` 提供对应 JSON 参数。例如只读查询上交所单日交易日历：

```bash
uv run python example/mcp/client/mcp_client.py \
  --server tushare_mcp \
  --tool trade_cal \
  --arguments '{"exchange":"SSE","start_date":"20260105","end_date":"20260105"}'
```

独立 Client 和累计 Agent 都会从 `.env` 展开配置；缺失变量或网络错误的终端信息不会显示 token。

代码统一放在 `src/melonclaw/`；运行时由 Agent 产生的中间材料放在项目根目录 `temp/` 下的临时子目录，进程退出时清理，不会覆盖学习笔记。

项目依赖由 `pyproject.toml` 和 `uv.lock` 管理；`requirements.txt` 保留用于需要 pip 的兼容场景。

### Interpreters：Agent loop 内的可编程工作区

累计 Agent 已加入官方 Beta 能力 `CodeInterpreterMiddleware`。它提供 `eval` 工具，在无宿主文件系统、网络、Shell、包管理器和真实时钟的 QuickJS 环境中执行 JavaScript。适合把循环、分支、并发和排序/分组/聚合从多轮模型调用压缩成一个小程序；它不是用来执行项目命令的 Sandbox，运行命令仍应使用 `execute`。

本项目使用 `mode="thread"`，因此可序列化的 Interpreter 变量会随 LangGraph 状态保存在当前会话中，并由已有 PostgreSQL Checkpointer 持久化。每个 CLI/Web `thread_id` 使用独立运行时；函数、类等不可序列化值不能依赖跨轮恢复。

Programmatic Tool Calling（PTC）只允许 `internet_search`：一次 `eval` 最多桥接 8 次搜索。文件、Shell 与 MCP 工具都没有加入 PTC 白名单，因为 PTC 内部工具调用不会逐次经过父 Agent 的 `interrupt_on`。Interpreter 可以通过顶层 `task(...)` 调度 Deep Agents 已配置的子 Agent；子 Agent 继续继承项目现有的工具审批配置。

启动时应看到：

```text
已启用 QuickJS Interpreter: eval（thread 模式；PTC 只读工具: internet_search；每次 eval 最多 8 次 PTC 调用）
```

可以用纯内存计算观察 `eval`，不需要搜索 API：

```text
🧑> 请使用 eval 计算 1 到 100 的平方和，并说明你使用的是 Interpreter 而不是 Shell。
```

预期工具时间线出现 `eval`，结果为 `338350`，且不会出现 Shell 审批。再用同一会话连续输入下面两轮，可观察 thread 级状态；新建 Web 会话则不应读到旧变量：

```text
🧑> 请用 eval 保存 globalThis.studyTopic = "Deep Agents Interpreters"，返回它。
🧑> 请用 eval 读取 globalThis.studyTopic。
```

### Human-in-the-loop：敏感操作人工审批

累计 Agent 现已启用 [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)。运行时文件的 `write_file`、`edit_file`、`delete` 会进入审批；Shell `execute` 也会暂停。读取文件、网页搜索和当前只读的 Tushare MCP 查询不受影响。`/skills/**` 通过 `CompositeBackend` 直接映射到仓库源目录，并以权限规则拒绝写入；该后端的 Shell 能力不提供沙箱隔离。

在模型提议敏感操作后，CLI 会展示工具、说明和已脱敏的参数，并等待显式决定：

```text
🛡️  [需要人工审批]

1. 工具: write_file
   参数:
{
  "file_path": "/report.md",
  "content": "..."
}
决定 [approve/edit/reject]>
```

- `approve`（或 `a`）：按原参数继续执行。
- `edit`（或 `e`）：输入完整的替换参数 JSON；工具名称保持不变。
- `reject`（或 `r`）：跳过执行，可附带原因，Agent 会收到反馈后调整方案。

CLI 和 Web 都使用 PostgreSQL Checkpointer；CLI 以固定的 `deepagents-quickstart` 作为 `thread_id`，因此退出并重启后仍可继续上下文和审批中断状态。Web 则以已校验的 `conversation_id` 作为 `thread_id`。在审批提示处按 `Ctrl-C` 或发送 EOF 不会执行该操作。

文件工具使用虚拟工作区：写入 `summary.md` 时应看到路径 `/summary.md`，它映射到本次进程的临时运行目录。Shell 仍是 LocalShellBackend 的能力且须人工审批；该后端没有沙箱隔离，因此 Web 仅适合本机开发，不能暴露给不受信任用户。写入后需要校验时，Agent 必须先等待 `write_file` 成功，再在下一轮调用 `read_file`，避免并发工具调用造成“文件未找到”的竞态。

应用还注册了 `FileOperationOrderingMiddleware`：若模型在同一批调用中对同一虚拟路径执行 `write_file`、`edit_file` 或 `delete`，同时又调用 `read_file`，中间件会保留写操作、取消该读取并返回提示 ToolMessage。模型只能在收到写操作结果后的下一轮再读取；不同路径和无依赖的工具调用仍会并行。

可用下面的请求观察流程（是否真的提出写入由模型决定）：

```text
🧑> 把本次研究的三条结论写入 /summary.md，写入前先告诉我将写什么。
```

预期顺序为：模型提出 `write_file` → CLI 显示 `🛡️` 和参数 → 输入 `a` / `r` / `e` → 相同 Agent 从中断处恢复并输出工具结果与后续回答。不会在未经决定时写入文件。
