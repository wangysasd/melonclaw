# Deep Agents 工具调用示例

运行：

```bash
uv run python example/chat/main.py
```

输入：

```text
请用工具计算 12 + 30
```

`main.py` 里只有一个简单工具：

```python
@tool
def add_numbers(a: int, b: int) -> int:
    return a + b

agent = create_deep_agent(
    model=build_model(),
    tools=[add_numbers],
)
```

本轮结束后，程序会打印模型生成的原始 `tool_call`，例如：

```text
{'name': 'add_numbers', 'args': {'a': 12, 'b': 30}, 'id': '...', 'type': 'tool_call'}
  name=add_numbers
  args={'a': 12, 'b': 30}
  id=...
```

如果模型决定调用工具，消息顺序会是：

```text
AIMessage(tool_calls=[...])
ToolMessage(tool_call_id='...', content='42')
AIMessage(content='答案是 42')
```

这里的 `ToolMessage` 由 Deep Agents 自动封装，代码只需要把 `add_numbers` 放进
`tools=[add_numbers]`；工具函数内部的 `[tool 执行]` 日志可以看到真正收到的参数。

重点 debug：

- `AIMessage.tool_calls`：模型想调用哪个工具、传了什么参数；
- `ToolMessage`：工具执行后的结果；
- `add_numbers(a, b)`：真正执行工具函数的位置。

工具调用参数是否出现，取决于模型是否判断这次请求需要使用工具。代码顶部的
`STREAM_OUTPUT` 可以切换流式和一次性输出。
