"""业务表结构的不变式。"""

from melonclaw.database.schema import (
    chat_attachments,
    chat_conversations,
    chat_messages,
    mcp_servers,
    metadata,
    model_configs,
    model_providers,
    projects,
    provider_user_keys,
    skill_user_states,
    skills,
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


def test_skills_table_scopes_and_unique_name():
    assert "scope" in skills.c
    assert "source_type" in skills.c
    assert "storage_path" in skills.c
    assert skills.c.name.unique is True
    assert any(
        constraint.name == "ck_skills_scope" for constraint in skills.constraints
    )
    assert any(
        constraint.name == "ck_skills_source_type"
        for constraint in skills.constraints
    )
    assert {fk.target_fullname for fk in skills.c.created_by.foreign_keys} == {
        "users.user_id"
    }


def test_skill_user_states_is_per_user_preference_overlay():
    """共享 Skill 个人启停偏好：(user_id, skill_name) 联合主键，两列都级联删除。"""
    assert [column.name for column in skill_user_states.primary_key.columns] == [
        "user_id",
        "skill_name",
    ]
    assert {fk.target_fullname for fk in skill_user_states.c.user_id.foreign_keys} == {
        "users.user_id"
    }
    assert {fk.target_fullname for fk in skill_user_states.c.skill_name.foreign_keys} == {
        "skills.name"
    }
    assert skill_user_states.c.enabled.nullable is False


def test_mcp_servers_stdio_requires_global_scope():
    assert mcp_servers.c.slug.unique is True
    assert "transport" in mcp_servers.c
    assert "tool_allowlist" in mcp_servers.c
    assert any(
        constraint.name == "ck_mcp_servers_stdio_scope"
        for constraint in mcp_servers.constraints
    )
    assert any(
        constraint.name == "ck_mcp_servers_transport"
        for constraint in mcp_servers.constraints
    )
    assert {fk.target_fullname for fk in mcp_servers.c.created_by.foreign_keys} == {
        "users.user_id"
    }


def test_model_providers_table_shape():
    assert model_providers.c.provider_key.unique is True
    assert "api_key" in model_providers.c
    assert "models_endpoint" in model_providers.c
    assert "version" in model_providers.c
    assert any(
        constraint.name == "ck_model_providers_scope"
        for constraint in model_providers.constraints
    )
    assert any(
        constraint.name == "ck_model_providers_source_type"
        for constraint in model_providers.constraints
    )
    assert {fk.target_fullname for fk in model_providers.c.created_by.foreign_keys} == {
        "users.user_id"
    }


def test_provider_user_keys_table_shape():
    assert "provider_key" in provider_user_keys.c
    assert "user_id" in provider_user_keys.c
    assert "api_key" in provider_user_keys.c
    assert {
        fk.target_fullname for fk in provider_user_keys.c.provider_key.foreign_keys
    } == {"model_providers.provider_key"}
    assert {fk.target_fullname for fk in provider_user_keys.c.user_id.foreign_keys} == {
        "users.user_id"
    }
    assert any(
        constraint.name == "uq_provider_user_keys"
        for constraint in provider_user_keys.constraints
    )


def test_model_configs_table_shape():
    assert model_configs.c.model_key.unique is True
    assert "provider_key" in model_configs.c
    assert "version" in model_configs.c
    assert "is_default" in model_configs.c
    assert "input_modalities" in model_configs.c
    assert any(
        constraint.name == "ck_model_configs_scope"
        for constraint in model_configs.constraints
    )
    assert any(
        constraint.name == "ck_model_configs_source_type"
        for constraint in model_configs.constraints
    )
    assert {fk.target_fullname for fk in model_configs.c.created_by.foreign_keys} == {
        "users.user_id"
    }
    # 模型必须挂在具体供应商下：供应商还有模型时删除被 RESTRICT 拒绝。
    assert {
        fk.target_fullname for fk in model_configs.c.provider_key.foreign_keys
    } == {"model_providers.provider_key"}
    assert (
        list(model_configs.c.provider_key.foreign_keys)[0].ondelete == "RESTRICT"
    )
    # 全表至多一行默认模型：部分唯一索引只在 is_default 为真时生效。
    default_indexes = [
        index
        for index in model_configs.indexes
        if index.name == "ux_model_configs_is_default"
    ]
    assert len(default_indexes) == 1
    assert default_indexes[0].unique is True
    assert default_indexes[0].dialect_options["postgresql"]["where"] is not None


def test_personal_default_index_is_scoped_to_creator():
    from melonclaw.database.schema import model_configs
    indexes = {index.name: index for index in model_configs.indexes}
    personal = indexes["ux_model_configs_user_default"]
    assert personal.unique
    assert [column.name for column in personal.columns] == ["created_by"]
    assert "scope = 'user'" in str(personal.dialect_options["postgresql"]["where"])
    assert "scope = 'global'" in str(indexes["ux_model_configs_is_default"].dialect_options["postgresql"]["where"])
