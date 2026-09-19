"""业务表结构的不变式。"""

from melonclaw.database.schema import (
    chat_attachments,
    user_interactions,
    user_tenants,
)


def test_redundant_business_columns_are_not_persisted():
    assert "id" not in user_tenants.c
    assert "kind" not in user_interactions.c
    assert "storage_key" not in chat_attachments.c


def test_user_tenants_uses_natural_composite_primary_key():
    assert {column.name for column in user_tenants.primary_key.columns} == {
        "user_id",
        "tenant_id",
    }
