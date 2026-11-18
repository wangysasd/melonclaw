"""知识库管理用例；所有 SQL 与事务由 repository 负责。"""

from melonclaw.core.knowledge import ChunkConfig, KnowledgeError


class KnowledgeService:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    @staticmethod
    def values(values):
        clean = dict(values)
        if "name" in clean:
            clean["name"] = clean["name"].strip()
            if not clean["name"]:
                raise KnowledgeError("知识库名称不能为空。")
        if "chunk_config" in clean and clean["chunk_config"] is not None:
            clean["chunk_config"] = ChunkConfig.model_validate(clean["chunk_config"]).model_dump()
        return clean

    async def create(self, user_id, values):
        clean = self.values(values)
        if "embedding_model_id" in clean:
            raise KnowledgeError("不支持指定内部服务配置。")
        if await self.storage.get_user_context(user_id) is None:
            raise KnowledgeError("当前用户不可用。")
        if clean.setdefault("knowledge_mode", "vector") != "vector":
            raise KnowledgeError("知识库类型无效。")
        clean["chunk_config"] = clean.get("chunk_config") or ChunkConfig().model_dump()
        clean.setdefault("share_scope", "tenant")
        clean["embedding_model_id"] = await self.storage.fixed_embedding_model_id(self.settings)
        return await self.storage.knowledge_create(user_id, clean)

    async def update(self, user_id, kb_id, version, values):
        return await self.storage.knowledge_update(user_id, kb_id, version, self.values(values))

    async def detail(self, user_id, kb_id):
        row = await self.storage.knowledge_detail(user_id, kb_id)
        row["published_document_count"] = await self.storage.knowledge_published_document_count(
            user_id, kb_id
        )
        return row
