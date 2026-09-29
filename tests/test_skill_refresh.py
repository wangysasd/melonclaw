"""真实 Deep Agent 图验证同一会话跨轮更新摘要，而不是只测返回字段。"""

import asyncio

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from melonclaw.middleware.skill_refresh import SkillRefreshMiddleware


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_same_checkpoint_uses_current_authorized_skill_catalog(tmp_path):
    directory = tmp_path / "skills/demo"
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text("---\nname: demo\ndescription: first\n---\n# Demo")
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    checkpointer = InMemorySaver()
    model = ToolModel(responses=[AIMessage(content="ok")])
    agent = create_deep_agent(model=model, backend=backend, skills=["/skills/"],
                             middleware=[SkillRefreshMiddleware(backend, ["/skills/"])],
                             checkpointer=checkpointer)
    agent.melonclaw_skill_references = []  # Compiled graph 支持应用层快照标记。
    config = {"configurable": {"thread_id": "same-thread"}}

    async def run():
        await agent.ainvoke({"messages": [{"role": "user", "content": "one"}]}, config)
        state = await agent.aget_state(config)
        assert state.values["skills_metadata"][0]["description"] == "first"
        path.write_text("---\nname: demo\ndescription: second\n---\n# Demo")
        await agent.ainvoke({"messages": [{"role": "user", "content": "two"}]}, config)
        state = await agent.aget_state(config)
        assert state.values["skills_metadata"][0]["description"] == "second"
        # 用空授权目录重新组装，但沿用同一 Checkpoint。
        empty = create_deep_agent(model=model, backend=backend,
                                 middleware=[SkillRefreshMiddleware(backend, [])],
                                 checkpointer=checkpointer)
        await empty.ainvoke({"messages": [{"role": "user", "content": "three"}]}, config)
        state = await empty.aget_state(config)
        assert state.values["skills_metadata"] == []

    asyncio.run(run())
