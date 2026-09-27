"""结构化用户问题的恢复路由。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.api.schemas import UserInputRequest
from melonclaw.api.sse import stream_response

router = APIRouter()


async def _accepted_receipt(
    *,
    interaction_id: str,
    decision_request_id: str,
    assistant_message_id: str,
):
    yield {
        "type": "user_input_accepted",
        "interaction_id": interaction_id,
        "decision_request_id": decision_request_id,
        "assistant_message_id": assistant_message_id,
    }
    yield {"type": "done", "terminal_reason": "already_accepted"}


@router.post(
    "/api/conversations/{conversation_id}/user-input",
    response_model=None,
    response_class=StreamingResponse,
)
async def submit_user_input(
    request: Request,
    conversation_id: UUID,
    payload: UserInputRequest,
) -> JSONResponse | StreamingResponse:
    manager = get_chat_service(request)
    if not manager.ready:
        return JSONResponse(await manager.status(), status_code=503)
    try:
        result = await manager.prepare_user_input(
            conversation_id,
            payload.user_id,
            payload.interaction_id,
            payload.assistant_message_id,
            str(payload.decision_request_id),
            payload.answer,
        )
    except Exception as exc:  # noqa: BLE001 - 准备阶段需要真实 HTTP 状态码
        return error_response(exc)

    if isinstance(result, dict):
        return stream_response(
            _accepted_receipt(
                interaction_id=result["interaction_id"],
                decision_request_id=result["decision_request_id"],
                assistant_message_id=result["assistant_message_id"],
            )
        )

    execution, command, accepted = result

    async def events():
        yield {
            "type": "user_input_accepted",
            "interaction_id": accepted["id"],
            "decision_request_id": accepted["decision_request_id"],
            "assistant_message_id": accepted["assistant_message_id"],
        }
        async for event in manager.stream_execution(execution, agent_input=command):
            yield event

    return stream_response(events())
