"""文档解析模型探针：固定图片、数据库共享凭据、有界响应和公网目的地防护。"""

import json

import httpx

from melonclaw.core.knowledge import KnowledgeError
from melonclaw.core.model_catalog import resolve_knowledge_model_row
from melonclaw.services.skill_remote import PublicDownloadTransport, validate_download_url

# 固定 2026 黑白测试图；不读取用户上传文件。
TEST_IMAGE = "data:image/png;base64,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"


async def test_model_connection(storage, user_id, model_key):
    context = await storage.get_user_context(user_id)
    if context is None or context.tenant_role not in {"admin", "owner"}:
        raise KnowledgeError("只有管理员可以测试知识库用途模型。")
    pair = await storage.get_model_with_provider(model_key)
    if not pair:
        raise KnowledgeError("模型不存在。")
    model, provider = pair
    if model["purpose"] == "embedding":
        raise KnowledgeError("内部服务不提供用户配置或连接测试入口。")
    resolved = resolve_knowledge_model_row(model, provider)
    key = resolved.api_key
    endpoint = str(provider["base_url"]).rstrip("/") + "/chat/completions"
    try:
        validate_download_url(endpoint)
    except ValueError:
        raise KnowledgeError(
            "模型测试需要无凭据的公网 HTTPS 端点；不允许内网、重定向或代理兜底。"
        ) from None
    payload = {"model": model["model_name"]}
    payload.update(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Read the four digits in this image. Return digits only.",
                    },
                    {"type": "image_url", "image_url": {"url": TEST_IMAGE}},
                ],
            }
        ],
        max_tokens=64,
        temperature=0,
    )
    headers = {**provider["request_headers"], "Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(
            timeout=resolved.timeout,
            trust_env=False,
            follow_redirects=False,
            transport=PublicDownloadTransport(limits=httpx.Limits(max_keepalive_connections=0)),
        ) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise KnowledgeError(
                        f"模型测试失败（HTTP {response.status_code}）；请检查端点、模型和共享 Key。"
                    )
                parts, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > 1024 * 1024:
                        raise KnowledgeError("模型测试响应过大。")
                    parts.append(part)
        result = json.loads(b"".join(parts))
        choice = result["choices"][0]
        if choice["finish_reason"] != "stop" or choice["message"]["content"].strip() != "2026":
            raise KnowledgeError("固定测试图片未正确识别或响应被截断。")
    except KnowledgeError:
        raise
    except Exception:
        raise KnowledgeError("模型测试失败，请检查网络、响应格式与供应商配置。") from None
    return {
        "ok": True,
        "purpose": model["purpose"],
        "summary": "固定图片识别成功；完整 OCR 链路尚未验收。",
    }
