"""实际 Deep Agents 图验证值不进入 Checkpoint、确认前不执行。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_skill_refresh import ToolModel

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
from melonclaw.services.environment_chat import parse_environment_chat
from melonclaw.tool.environment import build_environment_tools


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_real_environment_hitl_keeps_values_out_of_checkpoints(tmp_path, decision):
    async def run():
        context = AgentContext(
            user_id="alice",
            conversation_id=str(uuid4()),
            user_message_id=str(uuid4()),
            request_id="request",
            tenant_id="test",
            tenant_name="测试",
        )
        clean, draft = parse_environment_chat(
            "设置环境变量 TOKEN=fixture-sensitive-value",
            user_id="alice",
            conversation_id=context.conversation_id,
            request_id="request",
        )
        manifest = {
            "draft_id": draft["id"],
            "name": "TOKEN",
            "scope": "user",
            "action": "create",
            "expected_version": None,
            "replaces_value": True,
            "enabled": True,
            "description": "",
            "impact": "仅自己",
        }
        provider = SimpleNamespace(
            prepare=AsyncMock(return_value={"status": "prepared", "change": manifest}),
            confirm=AsyncMock(return_value={"status": "saved"}),
            inspect=AsyncMock(return_value={"items": []}),
        )
        tools = build_environment_tools(provider)
        for tool in tools:
            schema = tool.tool_call_schema.model_json_schema()["properties"]
            assert (
                not {"user_id", "tenant_id", "conversation_id", "runtime", "value"} & schema.keys()
            )
            assert tool.name not in INTERPRETER_PTC_TOOLS
        graph = create_deep_agent(
            model=ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "prepare_environment_change",
                                "id": "prepare",
                                "args": {"draft_id": draft["id"]},
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "confirm_environment_change",
                                "id": "confirm",
                                "args": {"change": manifest},
                            }
                        ],
                    ),
                    AIMessage(content="已处理"),
                ]
            ),
            tools=tools,
            backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
            context_schema=AgentContext,
            interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": context.conversation_id}}
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": clean}]}, config, context=context
        )
        assert result["__interrupt__"]
        provider.confirm.assert_not_awaited()
        snapshot = await graph.aget_state(config)
        assert "fixture-sensitive-value" not in str(snapshot)
        assert "value" not in json.dumps(manifest).replace("replaces_value", "replaces")
        await graph.ainvoke(
            Command(resume={"decisions": [{"type": decision}]}), config, context=context
        )
        assert provider.confirm.await_count == int(decision == "approve")

    asyncio.run(run())
