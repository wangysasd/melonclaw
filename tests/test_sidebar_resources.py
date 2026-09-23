"""侧栏资源排序游标与软删除字段。"""

import base64
import json
from datetime import datetime
from uuid import UUID

import pytest

from melonclaw.database.schema import chat_conversations, projects
from melonclaw.repository.mappers import decode_conversation_cursor, encode_conversation_cursor


def test_sidebar_resource_columns_are_persisted():
    assert "is_pinned" in projects.c
    assert "is_pinned" in chat_conversations.c
    assert "status" in chat_conversations.c


@pytest.mark.parametrize("is_pinned", [True, False])
def test_conversation_cursor_keeps_pin_partition(is_pinned: bool):
    identifier = "71ac03e3-a04f-408a-bde1-068371542d91"
    cursor = encode_conversation_cursor("2026-09-23T10:00:00+08:00", identifier, is_pinned)
    pinned, updated_at, conversation_id = decode_conversation_cursor(cursor)
    assert pinned is is_pinned
    assert updated_at == datetime.fromisoformat("2026-09-23T10:00:00+08:00")
    assert conversation_id == UUID(identifier)


def test_conversation_cursor_rejects_missing_pin_partition():
    old_payload = json.dumps({
        "updated_at": "2026-09-23T10:00:00+08:00",
        "id": "71ac03e3-a04f-408a-bde1-068371542d91",
    }).encode()
    with pytest.raises(ValueError, match="cursor 无效"):
        decode_conversation_cursor(base64.urlsafe_b64encode(old_payload).decode())
