"""开发控制的唯一嵌入契约；用户和数据库模型配置不能覆盖。"""

from urllib.parse import urlsplit
from uuid import UUID

import httpx
from langchain_openai import OpenAIEmbeddings

EMBEDDING_PROVIDER_KEY = "knowledge-embedding-internal"
EMBEDDING_MODEL_KEY = "knowledge-embedding-bge-m3"
EMBEDDING_MODEL_ID = UUID("ee5aba4a-8f73-4c87-866f-19b5dc0d1024")


def validate_embedding_settings(settings, *, require_key=True):
    from melonclaw.database.knowledge_schema import KnowledgeVector

    try:
        url = urlsplit(settings.knowledge_embedding_url)
        valid_url = (
            url.scheme == "https"
            and bool(url.hostname)
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
        )
    except ValueError:
        valid_url = False
    valid = (
        valid_url
        and bool(settings.knowledge_embedding_model_name.strip())
        and type(settings.knowledge_embedding_dimensions) is int
        and settings.knowledge_embedding_dimensions == KnowledgeVector.dimensions
    )
    if not valid or (require_key and not settings.knowledge_embedding_api_key.strip()):
        raise ValueError("知识库处理服务配置无效或与数据库向量结构不一致，请联系开发人员检查。")
    return settings


def build_knowledge_embeddings(settings) -> OpenAIEmbeddings:
    """显式注入开发配置；不读取个人 Key、模型目录或 SDK 默认凭据。"""
    validate_embedding_settings(settings)
    return OpenAIEmbeddings(
        model=settings.knowledge_embedding_model_name,
        api_key=settings.knowledge_embedding_api_key,
        base_url=settings.knowledge_embedding_url,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
        chunk_size=16,
        request_timeout=60,
        max_retries=0,
        http_client=httpx.Client(trust_env=False, follow_redirects=False),
        http_async_client=httpx.AsyncClient(trust_env=False, follow_redirects=False),
    )
