"""只注入根 Agent 的用户提问工具中间件。"""

from __future__ import annotations

from collections.abc import Iterable

from langchain.agents.middleware.types import AgentMiddleware

from melonclaw.tool.user_input import USER_INPUT_TOOL_NAME, ask_user

# 客户端能力协商：只有声明该能力的客户端才会拿到 ask_user。旧客户端不认识
# user_input_required 事件，拿到提问工具只会渲染出一张空卡片，用户不知道
# Agent 在等他，会话就挂死在那里。
USER_INPUT_CAPABILITY = "user_input_v1"

# 答案已收但这一轮没能跑完时，历史里给前端的错误码：不能自动重放（不知道副作用
# 执行到哪一步），只能由用户明确结束本轮后才允许继续。
USER_INPUT_RECOVERY_REQUIRED = "user_input_recovery_required"


def supports_user_input(capabilities: object) -> bool:
    """判断客户端能力清单是否声明了用户提问能力。

    未声明、类型异常或不可迭代时一律返回 False：拿不到能力就按“不支持”处理，
    宁可让 Agent 用普通文字追问，也不能发出渲染不出来的问题卡片。
    """

    if isinstance(capabilities, str) or not isinstance(capabilities, Iterable):
        return False
    try:
        return USER_INPUT_CAPABILITY in {str(item) for item in capabilities}
    except TypeError:
        return False


def normalize_capabilities(capabilities: object) -> tuple[str, ...]:
    """把客户端能力清单归一化为稳定有序的元组，用于缓存键和落库。"""

    if not supports_user_input(capabilities):
        return ()
    return (USER_INPUT_CAPABILITY,)


class UserInputMiddleware(AgentMiddleware):
    """把 ask_user 作为主 Agent 工具注入，默认 general-purpose 子 Agent 不继承。"""

    tools = (ask_user,)


__all__ = [
    "USER_INPUT_CAPABILITY",
    "USER_INPUT_RECOVERY_REQUIRED",
    "USER_INPUT_TOOL_NAME",
    "UserInputMiddleware",
    "normalize_capabilities",
    "supports_user_input",
]
