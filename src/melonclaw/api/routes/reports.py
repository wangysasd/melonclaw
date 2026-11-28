"""投诉举报接口。匿名入口不要求登录；管理入口沿用 /api/admin 的管理员校验。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from melonclaw.api.audit import endpoints
from melonclaw.api.dependencies import get_chat_service
from melonclaw.core.config import load_settings
from melonclaw.core.reports import ReportFlowError, report_channels
from melonclaw.services.reports import ReportService

router = APIRouter()
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Contact = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]
Reason = Literal[
    "cannot_login", "illegal", "harmful", "privacy",
    "infringement", "misinformation", "abuse", "other",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnonymousReport(Input):
    reason: Reason
    description: Text
    contact: Contact = ""


class ReportTarget(Input):
    kind: Literal["general", "assistant_message", "knowledge_document"]
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    kb_id: UUID | None = None
    document_id: UUID | None = None
    revision_id: UUID | None = None


class UserReport(AnonymousReport):
    target: ReportTarget


class Lookup(Input):
    public_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] = ""
    lookup_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""


class Note(Input):
    note: Text


class Reply(Input):
    feedback: Text


class Resolve(Input):
    feedback: Text
    measure: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def _service(request: Request) -> ReportService:
    storage = get_chat_service(request).storage
    if storage is None:
        raise ReportFlowError("unavailable", status=503, message="投诉服务暂不可用，请稍后重试。")
    limiter = getattr(request.app.state, "report_limiter", None)
    return ReportService(storage, limiter=limiter)


def _ip(request: Request) -> str:
    address, *_rest = endpoints(request.scope)
    return address or "unknown"


def _store(response: Response):
    response.headers["Cache-Control"] = "no-store"


@router.get("/api/reports/channels")
async def channels(response: Response):
    _store(response)
    return report_channels(load_settings())


@router.post("/api/reports/anonymous", status_code=201)
async def anonymous(request: Request, response: Response, body: AnonymousReport):
    _store(response)
    return await _service(request).submit_anonymous(
        ip=_ip(request), reason=body.reason, description=body.description, contact=body.contact,
    )


@router.post("/api/reports/lookup")
async def lookup(request: Request, response: Response, body: Lookup):
    _store(response)
    return await _service(request).lookup(
        ip=_ip(request), public_code=body.public_code, lookup_code=body.lookup_code,
    )


@router.post("/api/reports", status_code=201)
async def create_report(request: Request, response: Response, body: UserReport):
    _store(response)
    return await _service(request).submit(
        request.state.user.user_id,
        reason=body.reason,
        description=body.description,
        contact=body.contact,
        target=body.target.model_dump(mode="json"),
    )


@router.get("/api/reports")
async def mine(request: Request, response: Response):
    _store(response)
    return await _service(request).list_mine(request.state.user.user_id)


@router.get("/api/reports/{public_code}")
async def mine_one(request: Request, response: Response, public_code: str):
    _store(response)
    return await _service(request).get_mine(request.state.user.user_id, public_code)


@router.get("/api/admin/reports")
async def admin_reports(
    request: Request,
    response: Response,
    status: Literal["pending", "processing", "resolved"] | None = None,
    code: Annotated[str, Field(max_length=32)] = "",
):
    _store(response)
    return await _service(request).admin_list(status=status, public_code=code or None)


@router.get("/api/admin/reports/{report_id}")
async def admin_report(request: Request, response: Response, report_id: UUID):
    _store(response)
    return await _service(request).admin_get(report_id)


@router.post("/api/admin/reports/{report_id}/message")
async def admin_report_message(request: Request, response: Response, report_id: UUID):
    _store(response)
    return await _service(request).admin_read_message(report_id)


@router.post("/api/admin/reports/{report_id}/accept")
async def accept(request: Request, response: Response, report_id: UUID):
    _store(response)
    return await _service(request).accept(request.state.user.user_id, report_id)


@router.post("/api/admin/reports/{report_id}/note")
async def note(request: Request, response: Response, report_id: UUID, body: Note):
    _store(response)
    return await _service(request).note(request.state.user.user_id, report_id, body.note)


@router.post("/api/admin/reports/{report_id}/reply")
async def reply(request: Request, response: Response, report_id: UUID, body: Reply):
    _store(response)
    return await _service(request).reply(request.state.user.user_id, report_id, body.feedback)


@router.post("/api/admin/reports/{report_id}/resolve")
async def resolve(request: Request, response: Response, report_id: UUID, body: Resolve):
    _store(response)
    return await _service(request).resolve(
        request.state.user.user_id, report_id, body.feedback, body.measure,
    )
