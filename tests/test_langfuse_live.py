"""显式开启的真实 Langfuse 验收；每种策略用独立进程，正常检查不访问云端。"""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.skipif(os.getenv("MELONCLAW_TEST_LANGFUSE_LIVE") != "true", reason="显式开启才访问真实模型和 Langfuse")
@pytest.mark.parametrize("mode", ["enabled", "content-off", "disabled"])
def test_live_langfuse_policy(tmp_path, mode):
    result_path = tmp_path / f"{mode}.json"
    env = {**os.environ, "LANGFUSE_TRACING_ENABLED": "false" if mode == "disabled" else "true"}
    result = subprocess.run([sys.executable, __file__, mode, str(result_path)], env=env, capture_output=True, timeout=180)
    assert result.returncode == 0, f"live validation failed in mode {mode}; credentials and server bodies suppressed"
    record = json.loads(result_path.read_text())
    assert record["verified"]
    print(f"Langfuse live {mode}: {record.get('url', 'no client or exporter created')}")


def query_observations(settings, trace_id):
    env = {**os.environ, "LANGFUSE_PUBLIC_KEY": settings.langfuse_public_key,
           "LANGFUSE_SECRET_KEY": settings.langfuse_secret_key,
           "LANGFUSE_HOST": settings.langfuse_base_url, "LANGFUSE_BASE_URL": settings.langfuse_base_url}
    result = subprocess.run([
        "npx", "--yes", "langfuse-cli", "api", "observations", "list", "--trace-id", trace_id,
        "--fields", "core,basic,io,metadata,model,usage,trace_context", "--limit", "100", "--json",
    ], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, "Langfuse CLI failed"
    envelope = json.loads(result.stdout)
    assert envelope["status"] == 200, "Langfuse API request failed"
    return envelope["body"]["data"]


async def produce(mode, output_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from melonclaw.core.agent import AgentContext, build_research_agent
    from melonclaw.core.config import load_settings
    from melonclaw.core.tracing import Tracing, trace_config
    from melonclaw.database import Database
    from melonclaw.repository import BusinessRepository
    from melonclaw.services.model_resolution import ModelResolutionMixin

    settings = replace(load_settings(), langfuse_enabled=mode != "disabled",
                       langfuse_capture_content=mode != "content-off", langfuse_environment="test")
    client_calls = []
    if mode == "disabled":
        import langfuse

        def forbidden_client(**kwargs):
            client_calls.append(True)
            raise AssertionError("disabled client initialized")
        langfuse.Langfuse = forbidden_client
    tracing = Tracing(settings)
    run_id = str(uuid4())
    trace_id = tracing.client.create_trace_id(seed=run_id) if tracing.client else ""
    synthetic = "langfuse-opt-out-synthetic-private-text"
    db = None
    try:
        with tracing.run(feature=f"validation-{mode}", user_id="validation", tenant_id="validation",
                         conversation_id=run_id, run_id=run_id, input=synthetic) as trace:
            if mode == "enabled":
                class Resolver(ModelResolutionMixin):
                    def require_ready(self):
                        return self.storage
                db = Database(settings.database_url)
                await db.open()
                resolver = Resolver()
                resolver.settings, resolver.storage = settings, BusinessRepository(db)
                model = await resolver.resolve_model("admin", None)
                agent = await build_research_agent(
                    settings, workspace_dir=output_path.parent / "workspace", model=model,
                    checkpointer=InMemorySaver(),
                )
                result = await asyncio.wait_for(agent.ainvoke(
                    {"messages": [HumanMessage(content="监控验收：不要调用工具，不要读取文件，只回复：验证成功。") ]},
                    trace_config({"configurable": {"thread_id": run_id}}),
                    context=AgentContext(user_id="validation", tenant_id="validation", tenant_name="validation"),
                ), 90)
                trace.finish(output=result["messages"][-1].content)
            else:
                model = FakeMessagesListChatModel(responses=[AIMessage(
                    content=synthetic, usage_metadata={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
                )])
                result = await model.ainvoke([HumanMessage(content=synthetic)], trace_config({"metadata": {"ls_model_name": "validation-fixture"}}))
                trace.finish(output=result.content)
        if mode == "disabled":
            assert tracing.client is None and not client_calls and not trace_config().get("callbacks")
            output_path.write_text(json.dumps({"verified": True, "mode": mode}))
    finally:
        if db is not None:
            await db.close()
        await tracing.close()
    if mode == "disabled":
        return
    observations = []
    for _ in range(10):
        observations = query_observations(settings, trace_id)
        if observations:
            break
        time.sleep(1)
    roots = [row for row in observations if row.get("isRootObservation")]
    generations = [row for row in observations if row["type"] == "GENERATION"]
    assert len(roots) == 1 and generations
    assert all(row["model"] and row["usageDetails"] for row in generations)
    assert roots[0]["userId"] == "validation:validation" and roots[0]["sessionId"] == f"validation:{run_id}"
    if mode == "content-off":
        assert synthetic not in json.dumps(observations)
    for secret in [settings.langfuse_secret_key, settings.database_url, settings.deepseek_api_key]:
        assert not secret or secret not in json.dumps(observations)
    url = f"{settings.langfuse_base_url}/project/{roots[0]['projectId']}/traces/{trace_id}"
    output_path.write_text(json.dumps({"verified": True, "mode": mode, "url": url, "observations": len(observations)}))


if __name__ == "__main__":
    try:
        asyncio.run(produce(sys.argv[1], Path(sys.argv[2])))
    except Exception as exc:
        print("Live validation failed:", type(exc).__name__)
        raise SystemExit(1) from None
