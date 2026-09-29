"""启动时收敛遗留 pending 助手消息的约束。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from melonclaw.repository.conversations import ConversationRepositoryMixin
from melonclaw.services import runtime as runtime_module


class _RecordingConnection:
    """记录 execute 调用，供 UPDATE 语句断言。"""

    def __init__(self, statements, params):
        self._statements = statements
        self._params = params

    async def execute(self, statement):
        self._statements.append(str(statement))
        self._params.append(dict(statement.compile().params))
        return SimpleNamespace(rowcount=2)


class _RecordingTransaction:
    def __init__(self, statements, params):
        self._statements = statements
        self._params = params

    async def __aenter__(self):
        return _RecordingConnection(self._statements, self._params)

    async def __aexit__(self, exc_type, _exc, _traceback):
        return False


def _recording_mixin(statements, params):
    mixin = ConversationRepositoryMixin()
    mixin.engine = SimpleNamespace(
        begin=lambda: _RecordingTransaction(statements, params)
    )
    return mixin


def test_converge_only_touches_pending_assistant_messages():
    statements: list[str] = []
    params: list[dict] = []
    mixin = _recording_mixin(statements, params)

    count = asyncio.run(mixin.converge_stale_pending_messages())

    assert count == 2
    (sql,) = statements
    assert "UPDATE chat_messages" in sql
    # 只收敛 pending：等待审批/用户输入的 interrupted 轮次必须原样保留。
    assert "chat_messages.status" in sql
    assert "interrupted" not in sql
    assert "chat_messages.role" in sql
    (bound,) = params
    assert bound["status"] == "failed"
    assert bound["error_code"] == "stale_pending"
    assert bound["role_1"] == "assistant"
    assert bound["status_1"] == "pending"


def test_runtime_initialize_converges_stale_pending_after_schema_check(monkeypatch):
    calls: list[str] = []

    async def _converge():
        calls.append("converge")
        return 1

    fake_storage = SimpleNamespace(converge_stale_pending_messages=_converge)

    class _FakeDatabase:
        def __init__(self, url):
            calls.append("database")

        async def open(self):
            calls.append("open")

        async def verify_schema(self, **_kwargs):
            calls.append("verify")

        async def close(self):
            calls.append("close")

    async def _open_memory_store(url):
        calls.append("memory")
        return SimpleNamespace(), object()

    async def _open_checkpoint_pool(url):
        calls.append("checkpoint")
        return object()

    monkeypatch.setattr(
        runtime_module,
        "load_settings",
        lambda: SimpleNamespace(database_url="postgresql://unused"),
    )
    monkeypatch.setattr(runtime_module, "Database", _FakeDatabase)
    monkeypatch.setattr(
        runtime_module, "BusinessRepository", lambda database: fake_storage
    )
    monkeypatch.setattr(runtime_module, "open_memory_store", _open_memory_store)
    monkeypatch.setattr(runtime_module, "open_checkpoint_pool", _open_checkpoint_pool)
    monkeypatch.setattr(runtime_module, "AsyncPostgresSaver", lambda pool: object())
    monkeypatch.setattr(runtime_module, "MemoryService", lambda *args: object())

    rt = runtime_module.ChatRuntime()
    asyncio.run(rt.initialize())

    assert rt.startup_error is None
    # 清扫必须在 schema 校验之后、对外 ready 之前完成。
    assert calls == [
        "database",
        "open",
        "memory",
        "verify",
        "converge",
        "checkpoint",
    ]
