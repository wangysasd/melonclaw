"""本次准备调用独享的可信领域上下文；不跨请求或执行流缓存身份。"""

from dataclasses import dataclass
from uuid import UUID

from melonclaw.repository import ConversationNotFoundError, UserContext


@dataclass(frozen=True)
class PreparationContext:
    user: UserContext
    conversation: dict
    project: dict | None


async def prepare_context(
    conversations, conversation_id: UUID, user_id: str, user=None, *, storage=None
):
    context = user or await conversations.resolve_user(user_id)
    if context.user_id != user_id:
        raise ValueError("当前用户已变化。")
    storage = storage or conversations.runtime.require_ready()
    conversation = await storage.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise ConversationNotFoundError
    if conversation["user_id"] != user_id:
        raise ConversationNotFoundError
    project = None
    if conversation["project_id"] is not None:
        if not conversation.get("project_name") or not conversation.get("workdir_path"):
            raise ConversationNotFoundError
        project = {
            "id": conversation["project_id"],
            "user_id": user_id,
            "name": conversation["project_name"],
            "workdir_path": conversation["workdir_path"],
        }
    return PreparationContext(context, conversation, project)
