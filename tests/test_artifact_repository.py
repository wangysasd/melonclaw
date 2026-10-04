"""交付索引查询的投影与归属边界。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from melonclaw.repository.artifacts import ArtifactRepositoryMixin


def test_artifact_query_does_not_load_messages_and_rechecks_workspace_ownership():
    mid, cid = uuid4(), uuid4()
    row = {"ref": {"path": "/outputs/report.html"}, "message_id": mid, "created_at": datetime.now(UTC)}
    execute = AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row])))

    @asynccontextmanager
    async def connect():
        yield SimpleNamespace(execute=execute)

    repository = ArtifactRepositoryMixin()
    repository.engine = SimpleNamespace(connect=connect)
    items = asyncio.run(repository.list_artifacts(cid, "owner"))
    assert items == [{"ref": row["ref"], "message_id": str(mid), "created_at": row["created_at"].isoformat()}]
    execute.assert_awaited_once()
    statement = execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "chat_messages" not in sql
    assert "chat_conversations.user_id" in sql
    assert "chat_conversations.status" in sql
    assert "projects.status" in sql and "chat_conversations.user_id = projects.user_id" in sql
    assert "message_seq DESC" in sql
    assert "owner" in compiled.params.values()
    assert cid in compiled.params.values()
