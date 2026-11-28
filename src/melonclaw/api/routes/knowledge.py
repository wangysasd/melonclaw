"""知识库管理 API；Cookie 身份由全局依赖校验，目标成员另用 target_user_id。"""

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.core.knowledge import ChunkConfig, is_system_admin
from melonclaw.services.knowledge import KnowledgeService

router = APIRouter()
logger = logging.getLogger(__name__)


class BaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CreateBase(BaseRequest):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    knowledge_mode: Literal["vector"] = "vector"
    share_scope: Literal["everyone", "tenant", "selected"] = "tenant"
    selected_user_ids: list[str] = Field(default_factory=list, max_length=100)
    chunk_config: ChunkConfig | None = None


class UpdateBase(BaseRequest):
    version: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    share_scope: Literal["everyone", "tenant", "selected"] | None = None
    selected_user_ids: list[str] | None = Field(default=None, max_length=100)
    chunk_config: ChunkConfig | None = None

    @model_validator(mode="after")
    def reject_null(self):
        for key in self.model_fields_set - {"version"}:
            if getattr(self, key) is None:
                raise ValueError("更新字段不能为 null。")
        return self


class MemberRequest(BaseRequest):
    role: Literal["viewer", "editor"]


class TransferRequest(BaseRequest):
    target_user_id: str = Field(min_length=1, max_length=64)
    version: int = Field(gt=0)


class ShareScopeRequest(BaseRequest):
    version: int = Field(gt=0)
    share_scope: Literal["everyone", "tenant", "selected"]


class SuspendRequest(BaseRequest):
    version: int = Field(gt=0)
    reason: Literal["report_review", "risk_hold"]


class RestoreRequest(BaseRequest):
    version: int = Field(gt=0)
    reason: Literal["review_passed", "report_unfounded"]


class DisposeRequest(BaseRequest):
    version: int = Field(gt=0)
    reason: Literal["confirmed_violation"]


def storage(request):
    try:
        return get_chat_service(request).runtime.require_ready()
    except RuntimeError:
        raise HTTPException(503, "知识库服务尚未就绪，请检查数据库升级与服务状态。") from None


def public_result(value):
    if isinstance(value, list):
        return [public_result(item) for item in value]
    if isinstance(value, dict):
        return {k: public_result(v) for k, v in value.items() if k != "embedding_model_id"}
    return value


async def respond(awaitable, status=200):
    try:
        result = await awaitable
        return JSONResponse(
            jsonable_encoder(public_result(result or {"ok": True})), status_code=status
        )
    except Exception as exc:  # noqa: BLE001
        # SQL/SDK 异常不携带驱动请求与参数进入响应。
        if not isinstance(exc, (ValueError, LookupError, PermissionError)):
            logger.warning("knowledge api failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                {"error": "知识库服务暂不可用，请检查数据库升级与服务状态。"}, status_code=503
            )
        return error_response(exc)


@router.get("/api/knowledge-bases")
async def list_bases(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=255),
):
    return await respond(
        storage(request).knowledge_list(
            request.state.user.user_id, page=page, page_size=page_size, search=search
        )
    )


@router.post("/api/knowledge-bases")
async def create_base(request: Request, body: CreateBase):
    return await respond(
        KnowledgeService(storage(request), get_chat_service(request).runtime.settings).create(
            request.state.user.user_id, body.model_dump()
        ),
        201,
    )


@router.get("/api/knowledge-user-candidates")
async def user_candidates(request: Request, search: str = Query("", max_length=64)):
    return await respond(storage(request).knowledge_user_candidates(request.state.user.user_id, search=search))


@router.get("/api/knowledge-bases/{kb_id}")
async def detail_base(request: Request, kb_id: UUID):
    return await respond(
        KnowledgeService(storage(request), get_chat_service(request).runtime.settings).detail(
            request.state.user.user_id, kb_id
        )
    )


@router.patch("/api/knowledge-bases/{kb_id}")
async def update_base(request: Request, kb_id: UUID, body: UpdateBase):
    return await respond(
        KnowledgeService(storage(request), get_chat_service(request).runtime.settings).update(
            request.state.user.user_id,
            kb_id,
            body.version,
            body.model_dump(exclude={"version"}, exclude_unset=True),
        )
    )


@router.delete("/api/knowledge-bases/{kb_id}")
async def delete_base(request: Request, kb_id: UUID, version: int = Query(..., gt=0)):
    return await respond(
        storage(request).knowledge_delete(request.state.user.user_id, kb_id, version)
    )


@router.get("/api/knowledge-bases/{kb_id}/operations")
async def operation_history(
    request: Request,
    kb_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    user = request.state.user
    if not is_system_admin(getattr(user, "user_id", ""), getattr(user, "tenant_id", "")):
        return JSONResponse(
            {"error": "只有系统管理员可以查看知识库操作记录。"},
            status_code=403,
        )
    return await respond(
        storage(request).knowledge_operation_history(
            user.user_id, kb_id, page=page, page_size=page_size
        )
    )


@router.get("/api/knowledge-moderation/bases")
async def list_suspended_bases(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=255),
):
    return await respond(
        storage(request).knowledge_moderation_list(
            request.state.user.user_id, page=page, page_size=page_size, search=search
        )
    )


@router.post("/api/knowledge-bases/{kb_id}/suspend")
async def suspend_base(request: Request, kb_id: UUID, body: SuspendRequest):
    return await respond(
        storage(request).knowledge_suspend(
            request.state.user.user_id, kb_id, body.version, body.reason
        )
    )


@router.post("/api/knowledge-bases/{kb_id}/restore")
async def restore_base(request: Request, kb_id: UUID, body: RestoreRequest):
    return await respond(
        storage(request).knowledge_restore(
            request.state.user.user_id, kb_id, body.version, body.reason
        )
    )


@router.post("/api/knowledge-bases/{kb_id}/dispose")
async def dispose_base(request: Request, kb_id: UUID, body: DisposeRequest):
    return await respond(
        storage(request).knowledge_dispose(
            request.state.user.user_id, kb_id, body.version, body.reason
        )
    )


@router.get("/api/knowledge-bases/{kb_id}/members")
async def list_members(request: Request, kb_id: UUID):
    return await respond(storage(request).knowledge_members(request.state.user.user_id, kb_id))


@router.get("/api/knowledge-bases/{kb_id}/member-candidates")
async def candidates(request: Request, kb_id: UUID, search: str = Query("", max_length=64)):
    return await respond(
        storage(request).knowledge_members(
            request.state.user.user_id, kb_id, candidates=True, search=search
        )
    )


@router.put("/api/knowledge-bases/{kb_id}/members/{target_id}")
async def save_member(request: Request, kb_id: UUID, target_id: str, body: MemberRequest):
    return await respond(
        storage(request).knowledge_member_save(
            request.state.user.user_id, kb_id, target_id, body.role
        )
    )


@router.delete("/api/knowledge-bases/{kb_id}/members/{target_id}")
async def revoke_member(request: Request, kb_id: UUID, target_id: str):
    return await respond(
        storage(request).knowledge_member_save(request.state.user.user_id, kb_id, target_id, None)
    )


@router.put("/api/knowledge-bases/{kb_id}/share-scope")
async def set_share_scope(request: Request, kb_id: UUID, body: ShareScopeRequest):
    return await respond(
        storage(request).knowledge_set_share_scope(
            request.state.user.user_id, kb_id, body.version, body.share_scope
        )
    )


@router.post("/api/knowledge-bases/{kb_id}/transfer")
async def transfer(request: Request, kb_id: UUID, body: TransferRequest):
    return await respond(
        storage(request).knowledge_transfer(
            request.state.user.user_id, kb_id, body.target_user_id, body.version
        )
    )


# 导入和下载仍复用当前 Cookie 身份，不接受操作者 user_id。
def document_service(request):
    from melonclaw.services.knowledge_documents import KnowledgeDocuments
    return KnowledgeDocuments(get_chat_service(request).runtime.settings, storage(request))


@router.get("/api/knowledge-bases/{kb_id}/documents")
async def list_documents(request: Request, kb_id: UUID, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100)):
    return await respond(storage(request).knowledge_documents(
        request.state.user.user_id, kb_id, page=page, page_size=page_size))


@router.post("/api/knowledge-bases/{kb_id}/documents")
async def upload_document(request: Request, kb_id: UUID, file: UploadFile = File(...),
                          request_id: str = Form(..., min_length=1, max_length=128),
                          document_id: UUID | None = Form(None)):
    executor = getattr(request.app.state, "knowledge_executor", None)
    if executor is None or not executor.ready:
        await file.close()
        return JSONResponse({"error": "知识库处理服务尚未就绪。"}, status_code=503)
    return await respond(document_service(request).upload(
        request.state.user.user_id, kb_id, file, request_id, document_id), 202)


class RetryDocument(BaseRequest):
    request_id: str = Field(min_length=1, max_length=128)


@router.post("/api/knowledge-bases/{kb_id}/documents/{document_id}/retry")
async def retry_document(request: Request, kb_id: UUID, document_id: UUID, body: RetryDocument):
    return await respond(storage(request).knowledge_document_action(
        request.state.user.user_id, kb_id, document_id, "retry", body.request_id))


@router.post("/api/knowledge-bases/{kb_id}/documents/{document_id}/cancel")
async def cancel_document(request: Request, kb_id: UUID, document_id: UUID):
    return await respond(storage(request).knowledge_document_action(
        request.state.user.user_id, kb_id, document_id, "cancel"))


@router.delete("/api/knowledge-bases/{kb_id}/documents/{document_id}")
async def delete_document(request: Request, kb_id: UUID, document_id: UUID):
    return await respond(storage(request).knowledge_document_action(
        request.state.user.user_id, kb_id, document_id, "delete"))


@router.get("/api/knowledge-bases/{kb_id}/documents/{document_id}/source")
async def download_document(request: Request, kb_id: UUID, document_id: UUID):
    from fastapi.responses import FileResponse
    try:
        path, metadata = await document_service(request).download(request.state.user.user_id, kb_id, document_id)
        return FileResponse(path, filename=metadata["display_name"], media_type=metadata["mime_type"],
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                     "Content-Security-Policy": "default-src 'none'; sandbox"})
    except (ValueError, LookupError, PermissionError) as exc:
        return error_response(exc)


class SearchRequest(BaseRequest):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    top_k: int = Field(default=6, ge=1, le=20)
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)


def search_service(request):
    from melonclaw.services.knowledge_search import KnowledgeSearchService
    return KnowledgeSearchService(storage(request), get_chat_service(request).runtime.settings)


@router.post("/api/knowledge-bases/{kb_id}/search")
async def search_base(request: Request, kb_id: UUID, body: SearchRequest):
    values = body.model_dump()
    values["document_ids"] = [str(item) for item in body.document_ids]
    return await respond(search_service(request).search(request.state.user.user_id, kb_id, values))


@router.get("/api/knowledge-bases/{kb_id}/documents/{document_id}/text")
async def read_text(request: Request, kb_id: UUID, document_id: UUID,
                    revision_id: UUID | None = None, offset: int = Query(0, ge=0, le=2_147_483_647),
                    limit: int = Query(4000, ge=1, le=8000)):
    return await respond(search_service(request).open_text(request.state.user.user_id, kb_id,
        document_id, revision_id=revision_id, offset=offset, limit=limit))


