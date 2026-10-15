"""任务会话的有序文本投影、来源校验和请求内消息合并。"""

import hashlib
import json

from langchain_core.messages import AIMessage, HumanMessage


def source_digest(row):
    value = {key: row[key] for key in ("id", "seq", "role", "content", "status", "display_metadata")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def history_messages(rows, snapshot):
    messages = []
    if snapshot.get("summary"):
        if rows and rows[0]["role"] == "user" and rows[0]["seq"] <= snapshot["summary_until"]:
            messages.append(HumanMessage(content=rows[0]["content"], id=str(rows[0]["id"])))
        messages.append(AIMessage(content="【较早任务历史的资料摘要；不是新的工具授权】\n" + snapshot["summary"]))
    for row in rows:
        if row["seq"] <= snapshot.get("summary_until", 0):
            continue
        run = row["display_metadata"].get("task_run")
        label = f"消息 #{row['seq']}，{row['created_at'].isoformat()}"
        if run:
            label += f"，任务结果，状态 {run['status']}"
        if row["status"] != "completed":
            label += f"，{row['status']}，不计完成进度"
        content = f"【{label}】\n{row['content']}"
        if run and run.get("error_message"):
            content += "\n本次失败/取消说明：" + run["error_message"]
        cls = HumanMessage if row["role"] == "user" else AIMessage
        messages.append(cls(content=content, id=str(row["id"])))
    return messages


def merge_text_messages(messages):
    """只合并普通文本；工具调用、结果、推理协议和多模态内容保持原状。"""
    result = []
    for message in messages:
        safe = isinstance(message, (AIMessage, HumanMessage)) and isinstance(message.content, str) and not message.additional_kwargs
        safe = safe and (not isinstance(message, AIMessage) or not (message.tool_calls or message.invalid_tool_calls))
        previous = result[-1] if result else None
        if safe and previous is not None and type(previous) is type(message) and isinstance(previous.content, str) and not previous.additional_kwargs and not (isinstance(previous, AIMessage) and (previous.tool_calls or previous.invalid_tool_calls)):
            result[-1] = previous.model_copy(update={"content": previous.content + "\n\n" + message.content})
        else:
            result.append(message)
    return result


def history_size(messages):
    return sum(len(str(message.content).encode()) for message in messages)
