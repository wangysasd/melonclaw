# DeepSeek 联网搜索与网页读取

状态：已实现（2026-10-17）。

## 背景与目标

使用 DeepSeek Search/Fetch 替代 Tavily，提供公开网页检索和单页 Markdown 读取。
避免异常响应被误判为无结果，明确文本截断，并降低并发和暂时故障造成的失败。
接口契约见 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search)。

## 方案与数据流

`core/config.py` 从环境读取 `DEEPSEEK_API_KEY` 到 Settings，组装点将凭据绑定到工具。
工具不得自行读取环境变量，也不使用聊天模型的凭据。
普通聊天主/子 Agent 和 PTC 可调用 `internet_search`、`web_fetch`；
普通聊天主/子 Agent 始终可见这两个工具，不经过 find_tools，也不占动态工具池名额；主 Agent 的 PTC 沿用只读白名单，可直接使用这两个接口。单页读取优先直接调用 `web_fetch`。`eval` 通过最后一个表达式返回结果，不使用顶层 `return`。
后台任务仅装配受控异步 `internet_search`，不开放任意 URL 抓取。

输入校验 → 共用节流器 → 固定 DeepSeek 地址 → 有限重试 → 严格响应校验 → 限长输出。
共用协议与传输位于 `tool/deepseek.py`，聊天输出在 `tool/search.py`，
后台权限与错误映射在 `tool/task_search.py`。不新增持久化数据或依赖。

## 设计选择

- 聊天输入使用严格类型，拒绝布尔 count、数字字符串和字符串布尔值；
  query/ori_question 去除首尾空白后限 1–128 字符，提供空白原始问题时明确报错。
- 官方 count 支持 1–50；应用为控制模型上下文，聊天限制 1–20，后台限制 1–10，默认 5。
- 搜索响应必须有 results 数组；每条结果必须有字符串 title、url、summary。
  可选元数据按显式类型校验，忽略未声明的供应商调试字段。URL 限 8192 字符并保留完整来源。
  单条数据格式错误使本次请求失败，不静默丢弃；真正的空 results 是正常无匹配。
- 聊天搜索 title/snippet/summary 最多 500/500/2000 字符；need_content=true 时正文最多 8000。
  每条结果返回 truncated_fields，字段名出现表示内容已截断，不能视为完整原文。
  后台摘录优先 summary，其次 snippet/content，最多 8000；同样返回 truncated_fields，
  并保留整体 excerpts_truncated。Fetch 正文最多 32,000，返回 content_truncated。
- 搜索和抓取各自有一个进程内节流器，同步/异步及不同会话共享，开始请求间隔至少 0.21 秒。
  每次重试也需重新取得名额。锁不跨网络或等待，异步排队取消不遗留预留名额。
- 每次 HTTP 操作超时 30 秒，最多尝试 3 次。仅网络错误、超时、408、429、5xx 自动重试，
  基础等待 0.5/1 秒。其他 4xx、异常 JSON/响应结构不重试。
  Retry-After 支持秒数与 HTTP 日期；不早于服务端指定时间，最长等待 10 秒，
  超过上限或无法解析则直接失败，避免提前重试。重试可能延长一次工具调用的耗时；
  httpx 超时按连接/读取等阶段生效，不承诺整个工具固定 30 秒墙钟上限。

## 失败与安全边界

响应格式错误明确报错，不冒充空搜索/空网页；Fetch 空白正文也视为异常。
聊天仅返回脱敏错误说明；后台映射 search_failed，不保留远端异常链。
任务取消继续传播，外层任务总时限包括重试/排队，取消可中断异步等待及 HTTP 请求。
同步聊天工具仍使用框架线程执行，不能保证取消立即终止正在执行的同步网络调用。

这两个工具仅只读公开网页，沿用现有 HITL 免逐次审批及 PTC 白名单。
模型不能指定接口地址或凭据。Fetch 拒绝非 HTTP(S)、含用户名密码、本地与字面内网 IP 的 URL，
远端 DNS/重定向安全由 DeepSeek 服务执行；本地校验不等价于隔离沙箱。
进程内节流无法协调其他进程/应用使用同一账户，也无法消除供应商 Fetch 域名限流，
仍可能收到 429，按有限重试处理。不新增缓存、数据库表、后台任务抓取权限或旧协议兼容。

## 使用与验证

配置 `.env` 的 `DEEPSEEK_API_KEY` 并重新启动服务：`scripts/start.sh`。
模型仍在页面配置。让聊天搜索资料并读取某条链接；后台任务启用公开搜索即可。
未配置 Key 时调用明确失败，后台不开放搜索。不需要 db-init 或 db-update。

自动验证：`uv run pytest -q tests/test_deepseek.py tests/test_task_search.py tests/test_agent_tool_assembly.py tests/test_task_agent.py`。
覆盖严格输入、空凭据不回退、异常结构与空匹配、截断提示、完整 URL、重试上限、
永久错误、Retry-After、同步/异步节流以及取消。完整检查使用 `scripts/check.sh`。
真实外部验证依赖有效 DeepSeek Key 和网络，检查结果数量、正文长度和截断字段，
不输出请求头或凭据。

2026-10-17 验证完成：上述专项 131 项通过；`scripts/check.sh` 全部通过
（后端测试/架构与文档链接、lint、锁文件检查，前端 lint/类型检查/测试）。
真实聊天搜索返回 2 条带正文结果并正确标记截断；Fetch 返回 10,279 字符正文，
后台异步搜索返回 2 条结果。GPT-6 Luna / High 两轮只读审查完成，最终未发现明显功能回归。
未重启运行中的服务、未修改数据库，未执行真实模型自主搜索到回答的完整对话。
