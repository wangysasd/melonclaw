"""固定嵌入的内部外键引用；不作为模型调用配置，不保存凭据。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from melonclaw.core.knowledge import KnowledgeError
from melonclaw.core.knowledge_embedding import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_KEY,
    EMBEDDING_PROVIDER_KEY,
    validate_embedding_settings,
)
from melonclaw.database.schema import model_configs, model_providers
from melonclaw.repository.mappers import _now
from melonclaw.repository.resource_versions import resource_change


class KnowledgeEmbeddingRepositoryMixin:
    async def fixed_embedding_model_id(self, settings):
        validate_embedding_settings(settings, require_key=False)
        pair = await self.get_model_with_provider(EMBEDDING_MODEL_KEY)
        if pair is None:
            return await self._install_fixed_embedding_reference(settings)
        self._verify_fixed_embedding_reference(*pair, settings)
        return EMBEDDING_MODEL_ID

    @staticmethod
    def _verify_fixed_embedding_reference(model, provider, settings):
        valid = (
            model["id"] == EMBEDDING_MODEL_ID
            and model["purpose"] == "embedding"
            and model["scope"] == "global"
            and model["model_name"] == settings.knowledge_embedding_model_name
            and model["provider_key"] == EMBEDDING_PROVIDER_KEY
            and model["source_type"] == "system"
            and model["context_window"] is None
            and provider["provider_key"] == EMBEDDING_PROVIDER_KEY
            and provider["provider_type"] == "openai_compatible"
            and provider["source_type"] == "system"
            and provider["scope"] == "global"
            and provider["base_url"] == settings.knowledge_embedding_url
            and not provider["api_key"]
            and not provider["api_key_env"]
            and not provider["request_headers"]
            and not provider["extra_config"]
        )
        if not valid:
            raise KnowledgeError("知识库服务内部配置不一致，请联系开发人员检查。")

    @resource_change()
    async def _install_fixed_embedding_reference(self, settings):
        async with self.engine.begin() as conn:
            await self._model_catalog_lock(conn)
            now = _now()
            await conn.execute(
                insert(model_providers)
                .values(
                    id=uuid4(),
                    provider_key=EMBEDDING_PROVIDER_KEY,
                    scope="global",
                    source_type="system",
                    display_name="知识库内部服务",
                    provider_type="openai_compatible",
                    base_url=settings.knowledge_embedding_url,
                    api_key=None,
                    api_key_env="",
                    request_headers={},
                    extra_config={},
                    enabled=True,
                    created_by="admin",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[model_providers.c.provider_key])
            )
            await conn.execute(
                insert(model_configs)
                .values(
                    id=EMBEDDING_MODEL_ID,
                    model_key=EMBEDDING_MODEL_KEY,
                    provider_key=EMBEDDING_PROVIDER_KEY,
                    scope="global",
                    source_type="system",
                    purpose="embedding",
                    display_name="知识库内部服务",
                    model_name=settings.knowledge_embedding_model_name,
                    context_window=None,
                    input_modalities=["text"],
                    enabled=True,
                    is_default=False,
                    created_by="admin",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[model_configs.c.model_key])
            )
            # 键冲突必须校验，不覆盖原配置或静默改变已有库绑定。
            pair = await self.get_model_with_provider(EMBEDDING_MODEL_KEY, connection=conn)
            self._verify_fixed_embedding_reference(*pair, settings)
            return EMBEDDING_MODEL_ID

    async def assert_fixed_embedding_reference(self, conn, model_id):
        if model_id != EMBEDDING_MODEL_ID:
            raise KnowledgeError("知识库内部服务引用不匹配，请联系开发人员检查。")
        row = await conn.scalar(select(model_configs.c.id).where(model_configs.c.id == model_id))
        if row is None:
            raise KnowledgeError("知识库服务尚未就绪，请联系开发人员检查。")
