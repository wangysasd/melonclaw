"""真实聊天执行链路验证环境变量脱敏、审批与重放。"""

import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from test_environment_api import environment_api
from test_langfuse_chat_postgres import chat_runtime
from test_skill_refresh import ToolModel

from melonclaw.core import agent as agent_module
from melonclaw.database.schema import environment_operations
from melonclaw.services.environment import environment_scope
from melonclaw.services.environment_chat import parse_environment_chat

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 PostgreSQL 测试连接",
)


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    ("user_id", "decision", "scope"),
    [
        ("admin", "approve", "global"),
        ("admin", "reject", "global"),
        ("alice", "approve", "user"),
        ("alice", "reject", "user"),
    ],
)
def test_chat_environment_lifecycle_uses_real_checkpoint_and_repository(
    monkeypatch, tmp_path, user_id, decision, scope, batch
):
    async def run():
        raw_value = f"synthetic-environment-secret-{user_id}"
        variable_name = f"CHAT_{user_id.upper()}_TOKEN"

        async with environment_api() as (_, storage):
            async with chat_runtime(storage, tmp_path, None) as chat:
                conversation = await chat.create_conversation(user_id)
                conversation_id = UUID(conversation["id"])
                request_id = str(uuid4())
                raw_message = f"设置环境变量 {variable_name}={raw_value}"
                if batch:
                    raw_message = ("请安装全部技能 https://skills.example/download?name=one\n"
                                   "并配置以下永久环境变量（已有则替换）\n"
                                   f"{variable_name}={raw_value}\n{variable_name}_SECOND={raw_value}-second")
                clean, draft = parse_environment_chat(
                    raw_message,
                    user_id=user_id,
                    conversation_id=str(conversation_id),
                    request_id=request_id,
                )
                assert raw_value not in clean

                user_context = await storage.get_user_context(user_id)
                assert environment_scope(user_context) == scope
                manifest = {
                    "draft_id": draft["id"],
                    "name": variable_name,
                    "scope": scope,
                    "action": "create",
                    "expected_version": None,
                    "replaces_value": True,
                    "enabled": True,
                    "description": "",
                    "impact": (
                        "系统共享，影响使用该共享变量的所有用户"
                        if scope == "global"
                        else "仅自己；停用或删除后自动使用可用共享配置"
                    ),
                }
                if batch:
                    item = {key: value for key, value in manifest.items() if key != "draft_id"}
                    manifest = {"draft_id": draft["id"], "scope": scope, "changes": [item, {**item, "name": variable_name + "_SECOND"}]}
                model = ToolModel(
                    responses=[
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "prepare_environment_change",
                                    "id": "prepare-env",
                                    "args": {"draft_id": draft["id"]},
                                }
                            ],
                        ),
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "confirm_environment_change",
                                    "id": "confirm-env",
                                    "args": {"change": manifest},
                                }
                            ],
                        ),
                        AIMessage(content="已完成环境变量设置请求。"),
                    ]
                )
                monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: model)

                prepared = await chat.prepare_message(
                    conversation_id,
                    user_id,
                    request_id,
                    raw_message,
                    model_id="custom:offline",
                )
                assert prepared.content == clean
                assert raw_value not in prepared.content
                assert await storage.effective_environment(user_id) == {}

                pending_events = [event async for event in chat.stream_execution(prepared)]
                approval_event = next(
                    event for event in pending_events if event["type"] == "approval_required"
                )
                assert raw_value not in json.dumps(pending_events, ensure_ascii=False)
                approval = approval_event["request"]
                operation = await storage.environment_operation(
                    draft["id"], user_id, str(conversation_id)
                )
                assert operation["status"] == "prepared"
                assert operation["manifest"]["scope"] == scope
                assert await storage.effective_environment(user_id) == {}

                _, messages, _ = await storage.list_messages(
                    conversation_id, user_id, limit=10, before_seq=None
                )
                assert len(messages) == 2
                assert all(
                    raw_value not in json.dumps(item, ensure_ascii=False) for item in messages
                )
                checkpoint = await prepared.agent.aget_state(prepared.config)
                assert raw_value not in str(checkpoint)

                resumed, command = await chat.prepare_approval(
                    conversation_id,
                    user_id,
                    [{"type": decision}],
                    approval_batch_id=UUID(approval["approval_batch_id"]),
                    assistant_message_id=UUID(approval["assistant_message_id"]),
                )
                if decision == "approve":
                    assert await storage.effective_environment(user_id) == {}
                    staged = await storage.environment_operation(
                        draft["id"], user_id, str(conversation_id)
                    )
                    assert staged["status"] == "prepared" and staged["payload"] is not None
                else:
                    async with storage.engine.connect() as conn:
                        rejected = dict(
                            (
                                await conn.execute(
                                    select(environment_operations).where(
                                        environment_operations.c.id == UUID(draft["id"]),
                                        environment_operations.c.user_id == user_id,
                                        environment_operations.c.conversation_id == conversation_id,
                                    )
                                )
                            )
                            .mappings()
                            .one()
                        )
                    assert rejected["status"] == "rejected" and rejected["payload"] is None

                resumed_events = [
                    event async for event in chat.stream_execution(resumed, agent_input=command)
                ]
                assert resumed_events[-1]["type"] == "done"
                all_events = pending_events + resumed_events
                assert raw_value not in json.dumps(all_events, ensure_ascii=False)

                effective = await storage.effective_environment(user_id)
                if decision == "approve":
                    assert effective[variable_name] == raw_value
                    if batch:
                        assert effective[variable_name + "_SECOND"] == raw_value + "-second"
                    committed = await storage.environment_operation(
                        draft["id"], user_id, str(conversation_id)
                    )
                    assert committed["status"] == "committed"
                    assert committed["payload"] is None
                else:
                    assert variable_name not in effective
                    async with storage.engine.connect() as conn:
                        rejected = dict(
                            (
                                await conn.execute(
                                    select(environment_operations).where(
                                        environment_operations.c.id == UUID(draft["id"]),
                                        environment_operations.c.user_id == user_id,
                                        environment_operations.c.conversation_id == conversation_id,
                                    )
                                )
                            )
                            .mappings()
                            .one()
                        )
                    assert rejected["status"] == "rejected" and rejected["payload"] is None

                _, final_messages, _ = await storage.list_messages(
                    conversation_id, user_id, limit=10, before_seq=None
                )
                assert len(final_messages) == 2
                assert all(
                    raw_value not in json.dumps(item, ensure_ascii=False) for item in final_messages
                )
                final_checkpoint = await prepared.agent.aget_state(prepared.config)
                assert raw_value not in str(final_checkpoint)

                # The same request replays its stored result and does not stage
                # or execute the environment mutation a second time.
                replay = await chat.prepare_message(
                    conversation_id,
                    user_id,
                    request_id,
                    raw_message,
                    model_id="custom:offline",
                )
                replay_events = [event async for event in chat.stream_execution(replay)]
                assert any(event.get("replayed") for event in replay_events)
                assert len(await storage.list_environment(user_id)) == int(decision == "approve") * (2 if batch else 1)
                async with storage.engine.connect() as conn:
                    operation_count = await conn.scalar(
                        select(func.count())
                        .select_from(environment_operations)
                        .where(environment_operations.c.user_id == user_id)
                    )
                assert operation_count == 1

    asyncio.run(run())
