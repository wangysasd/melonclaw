"""服务异常到 HTTP 状态的映射：用户能自己纠正的失败不该报成 500。"""

import json

from melonclaw.api.errors import error_response
from melonclaw.services.errors import AgentExecutionError


def test_agent_execution_error_defaults_to_500():
    """其余几处 AgentExecutionError 确实不可恢复，必须保持 500。"""

    response = error_response(AgentExecutionError("Agent 未返回可保存的助手回复。"))

    assert response.status_code == 500


def test_agent_execution_error_can_opt_into_4xx():
    """已选技能在执行前被移除时，用户重选一个就能继续，不是服务端故障。"""

    response = error_response(
        AgentExecutionError(
            "选择的技能已不可用，请重新选择技能后重试。", status_code=409
        )
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "error": "选择的技能已不可用，请重新选择技能后重试。"
    }
