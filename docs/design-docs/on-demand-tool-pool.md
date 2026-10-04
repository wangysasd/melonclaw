# 按需官方工具选择与会话池

状态：已实现；核实版本：Deep Agents 0.7.5、LangChain 1.3.14。

## 背景与目标
每消息预选增加等待，用户希望采用官方选择算法，但仅在当前工具不足时调用。目标是普通聊天零选择调用、跨消息复用工具池，同时保持会话隔离与审批边界。

## 方案与关键取舍
官方 LLMToolSelectorMiddleware 负责结构化 schema、选名校验及数量限制。项目 ToolPoolMiddleware 负责生命周期，不维护第二套 JSON 选择解析器。find_tools 只提交需求；下一次 before_model 以需求为 HumanMessage 委托官方公开 awrap_model_call，handler 只收集工具名。选择器不会每轮包住主模型。

应用工具在 `tool/tools.py` 装配时统一返回 LangChain `BaseTool`；普通搜索函数通过 `StructuredTool.from_function` 生成名称、描述和参数 schema，工具池与主/子 Agent 共用这些定义。选择器先于 Agent 图构建，必须在装配入口完成包装。直接传入普通函数会在读取 `.name` 时失败，连普通问答也无法开始；重新同步只读取会话状态，修复后需重启 Web 服务加载新代码。

池默认 16 个业务工具，每次最多选 8 个，每用户消息最多处理 4 个请求；同一模型轮次的并行请求合成一次选择。满时淘汰最早选择或使用的工具。基础/管理工具始终可见，不占池容量。参数从 core/config.py 进入，见 README；代码和 .env.example 提供默认值。

## 数据与事件流
用户任务 → 主模型使用当前 schema → 能力不足时 find_tools(query) → Command 追加带 turn_id/call_id 的请求 → before_model 选择 → Checkpoint 保存 pool 和 processed → 主模型使用扩充后的工具。选择阶段通过已有 custom 事件发出 selecting_tools、selection started/completed 与 waiting_model；官方内部调用在独立异步 config context 标记 TAG_NOSTREAM，内部结构化 JSON 不进入正文。

池跨用户消息保留；请求预算每消息重置，审批恢复使用相同用户消息 ID。目录摘要覆盖全部已授权工具名称、描述和 schema，变化清空池且不重放旧请求。主/子 Agent 都显式装配池，但 PrivateStateAttr 阻止请求/池状态在父子间复制或回写；共享 Agent 实例只持有不可变目录和配置。

工具可见性由 `ToolPoolMiddleware.filter_request` 统一解析。主／子 Agent 的官方摘要扩展在计数前复用该过滤，隐藏目录不占摘要预算；主模型继续使用同一规则，工具入池后其 schema 正常计入上下文。完整授权目录仍只用于按需选择，不因摘要过滤而删除工具或改变审批边界。

## 失败与安全边界
输入长度 1～512，拒绝空白需求。并行请求由 reducer 去重，在 before_model 统一截取预算；超出预算不进行选择。选择失败、超时或空结果不扩大可见工具；已处理请求不会自动重复，但 Agent 可以在预算内提交新的需求。供应商必须支持官方所用结构化输出，未支持时明确降级，不回退自定义 JSON 路由。取消传播，不写入凭据或异常原文。

find_tools 是内部状态请求，不执行外部工具、不改变授权，不进入审批清单或 PTC。选择工具只来自本次已授权目录；模型 schema 可见性不替代权限控制，原 MCP 白名单、HITL、文件边界继续执行。LocalShellBackend 仍非安全沙箱。无新增业务表，不维护旧 Checkpoint 字段兼容。

## 运行与验证
修改 .env 后重启现有 Web 服务，无需 db-init。配置模型后先发送简单问答，预计无“选择工具”；随后提出需要 MCP 能力的任务，缺工具时出现选择阶段；下一条使用相同工具的任务复用池。修改 MCP/Skill 所影响的工具目录后重新构建 Agent，池应失效。查看选择失败时的阶段与可见工具，不打印请求凭据。

自动验证覆盖无请求、官方真实 OpenAI-compatible JSON schema 请求、需求转交、跨消息/会话、审批恢复、目录变更、容量淘汰、并行预算、输入校验、超时、取消、v3 文本隔离和主/子图装配。实际供应商结构化输出、真实 PostgreSQL 与外部 MCP 需部署环境验证。

`tests/test_agent_tool_assembly.py` 通过实际 `build_research_agent` 入口装配应用工具，离线替换模型和 Tavily 传输，验证普通问答零选择、按需选择后执行内置搜索、子 Agent 独立发现搜索且父池不被覆盖，防止只测试已包装工具而漏检实际装配路径。

验证结果：`scripts/check.sh` 全部通过，覆盖后端编译、pytest、ruff、锁文件一致性、前端 lint/类型/测试与文档链接。真实 Deep Agents 父子调用已验证子池不继承或覆盖主池。
