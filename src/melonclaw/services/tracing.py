"""聊天执行的监控适配；业务事件仍由既有执行服务产生。"""

from contextlib import aclosing


async def trace_chat_execution(runtime, execution, events):
    if runtime.tracing is None or execution.replay_message is not None:
        async with aclosing(events):
            async for event in events:
                yield event
        return
    with runtime.tracing.run(
        feature="chat-turn", user_id=execution.user_id, tenant_id=execution.tenant_id,
        conversation_id=str(execution.conversation_id), run_id=execution.run_id,
        input=execution.content if not execution.resuming else {"resuming": True},
        metadata={"project_id": str(execution.project_id or ""),
                  "model_id": execution.model.profile_id,
                  "request_id": execution.request_id,
                  "assistant_message_id": str(execution.assistant_message_id),
                  "resuming": execution.resuming},
    ) as trace:
        async with aclosing(events):
            async for event in events:
                kind = event.get("type")
                if kind == "completed":
                    trace.finish(output=event.get("content"))
                elif kind in {"approval_required", "approval", "user_input_required"}:
                    trace.finish(status="interrupted")
                elif kind == "message_status":
                    trace.finish(status=event.get("status", "failed"))
                elif kind == "error":
                    trace.finish(status="failed")
                yield event
