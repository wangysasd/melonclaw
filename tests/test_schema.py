"""业务表结构的不变式。"""

from melonclaw.database.schema import (
    chat_attachments,
    chat_conversations,
    chat_messages,
    metadata,
    projects,
    user_interactions,
    users,
)


def test_redundant_business_columns_are_not_persisted():
    assert "user_tenants" not in metadata.tables
    assert "kind" not in user_interactions.c
    assert "storage_key" not in chat_attachments.c


def test_user_has_one_required_tenant():
    assert users.c.tenant_id.nullable is False
    assert {fk.target_fullname for fk in users.c.tenant_id.foreign_keys} == {
        "tenants.tenant_id"
    }
    assert "tenant_role" in users.c
    assert "tenant_status" in users.c


def test_assistant_steps_are_embedded_in_chat_messages():
    assert "assistant_steps" in chat_messages.c
    assert "execution_duration_ms" in chat_messages.c


def test_conversation_id_is_the_primary_key_and_project_is_optional():
    assert [column.name for column in chat_conversations.primary_key.columns] == ["id"]
    assert chat_conversations.c.project_id.nullable is True
    assert "conversation_id" not in chat_conversations.c
    assert "is_default" not in projects.c


def test_attachment_has_exactly_one_workspace_owner():
    assert chat_attachments.c.project_id.nullable is True
    assert chat_attachments.c.owner_conversation_id.nullable is True
    assert any(
        constraint.name == "ck_attachment_owner"
        for constraint in chat_attachments.constraints
    )
