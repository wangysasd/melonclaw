"""投诉举报表。不外键到消息或文档，避免删除内容时连带删掉工单。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID


def register_report_tables(metadata):
    """注册投诉主表和操作历史。调用方负责挂到业务 metadata。"""

    reports = Table(
        "reports",
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("public_code", String(32), nullable=False),
        Column("status", String(16), nullable=False),
        Column("kind", String(32), nullable=False),
        Column("reason", String(32), nullable=False),
        Column("description", Text, nullable=False),
        Column("contact", String(80)),
        Column("submitter_user_id", String(64)),
        Column("lookup_code_hash", String(64)),
        Column("conversation_id", UUID(as_uuid=True)),
        Column("message_id", UUID(as_uuid=True)),
        Column("message_seq", Integer),
        Column("message_created_at", DateTime(timezone=True)),
        Column("kb_id", UUID(as_uuid=True)),
        Column("document_id", UUID(as_uuid=True)),
        Column("revision_id", UUID(as_uuid=True)),
        Column("revision_no", Integer),
        Column("source_hash", String(64)),
        Column("target_label", String(255), nullable=False, server_default=""),
        Column("content_sha256", String(64)),
        Column("excerpt", String(160), nullable=False, server_default=""),
        Column("char_count", Integer),
        Column("assignee_user_id", String(64)),
        Column("accepted_at", DateTime(timezone=True)),
        Column("resolved_at", DateTime(timezone=True)),
        Column("public_feedback", Text),
        Column("measure", String(500)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("public_code ~ '^MC-[0-9A-F]{8}$'", name="ck_reports_code"),
        CheckConstraint(
            "status IN ('pending','processing','resolved')", name="ck_reports_status"
        ),
        CheckConstraint(
            "kind IN ('general','assistant_message','knowledge_document')",
            name="ck_reports_kind",
        ),
        CheckConstraint(
            "reason IN ('cannot_login','illegal','harmful','privacy',"
            "'infringement','misinformation','abuse','other')",
            name="ck_reports_reason",
        ),
        CheckConstraint(
            "char_length(description) BETWEEN 1 AND 2000", name="ck_reports_description"
        ),
        CheckConstraint(
            "contact IS NULL OR (char_length(contact) BETWEEN 1 AND 80 "
            "AND contact !~ '[[:cntrl:]]')",
            name="ck_reports_contact",
        ),
        CheckConstraint("char_length(excerpt) <= 160", name="ck_reports_excerpt"),
        CheckConstraint("char_length(target_label) <= 255", name="ck_reports_label"),
        CheckConstraint(
            "lookup_code_hash IS NULL OR lookup_code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_reports_lookup_hash",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_reports_content_hash",
        ),
        CheckConstraint(
            "source_hash IS NULL OR source_hash ~ '^[0-9a-f]{64}$'",
            name="ck_reports_source_hash",
        ),
        CheckConstraint(
            "(submitter_user_id IS NULL) = (lookup_code_hash IS NOT NULL)",
            name="ck_reports_identity",
        ),
        CheckConstraint(
            "(kind = 'general' AND conversation_id IS NULL AND message_id IS NULL "
            "AND kb_id IS NULL AND document_id IS NULL AND revision_id IS NULL "
            "AND content_sha256 IS NULL) "
            "OR (kind = 'assistant_message' AND conversation_id IS NOT NULL "
            "AND message_id IS NOT NULL AND kb_id IS NULL AND document_id IS NULL "
            "AND revision_id IS NULL AND content_sha256 IS NOT NULL "
            "AND submitter_user_id IS NOT NULL) "
            "OR (kind = 'knowledge_document' AND kb_id IS NOT NULL "
            "AND document_id IS NOT NULL AND revision_id IS NOT NULL "
            "AND revision_no IS NOT NULL AND source_hash IS NOT NULL "
            "AND conversation_id IS NULL AND message_id IS NULL "
            "AND content_sha256 IS NOT NULL AND submitter_user_id IS NOT NULL)",
            name="ck_reports_target",
        ),
        CheckConstraint(
            "public_feedback IS NULL OR char_length(public_feedback) BETWEEN 1 AND 2000",
            name="ck_reports_feedback",
        ),
        CheckConstraint(
            "measure IS NULL OR char_length(measure) BETWEEN 1 AND 500",
            name="ck_reports_measure",
        ),
        CheckConstraint("char_count IS NULL OR char_count >= 0", name="ck_reports_chars"),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (accepted_at IS NULL OR accepted_at >= created_at) "
            "AND (resolved_at IS NULL OR (accepted_at IS NOT NULL "
            "AND resolved_at >= accepted_at))",
            name="ck_reports_times",
        ),
        CheckConstraint(
            "(status = 'pending' AND assignee_user_id IS NULL AND accepted_at IS NULL "
            "AND resolved_at IS NULL AND public_feedback IS NULL AND measure IS NULL) "
            "OR (status = 'processing' AND assignee_user_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND resolved_at IS NULL AND measure IS NULL) "
            "OR (status = 'resolved' AND assignee_user_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND resolved_at IS NOT NULL "
            "AND public_feedback IS NOT NULL AND char_length(btrim(public_feedback)) > 0 "
            "AND measure IS NOT NULL AND char_length(btrim(measure)) > 0)",
            name="ck_reports_state",
        ),
        Index("uq_reports_public_code", "public_code", unique=True),
        Index("ix_reports_submitter_created", "submitter_user_id", "created_at"),
        Index("ix_reports_status_updated", "status", "updated_at"),
        Index(
            "uq_reports_open_message",
            "submitter_user_id",
            "message_id",
            unique=True,
            postgresql_where=text(
                "message_id IS NOT NULL AND status IN ('pending', 'processing')"
            ),
        ),
        Index(
            "uq_reports_open_revision",
            "submitter_user_id",
            "revision_id",
            unique=True,
            postgresql_where=text(
                "revision_id IS NOT NULL AND status IN ('pending', 'processing')"
            ),
        ),
    )
    events = Table(
        "report_events",
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column(
            "report_id",
            UUID(as_uuid=True),
            ForeignKey("reports.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("actor_user_id", String(64)),
        Column("action", String(16), nullable=False),
        Column("internal_note", Text),
        Column("public_feedback", Text),
        Column("measure", String(500)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint(
            "action IN ('submitted','accepted','noted','replied','resolved')",
            name="ck_report_events_action",
        ),
        CheckConstraint(
            "(action IN ('submitted','accepted') AND internal_note IS NULL "
            "AND public_feedback IS NULL AND measure IS NULL) "
            "OR (action = 'noted' AND internal_note IS NOT NULL "
            "AND char_length(internal_note) BETWEEN 1 AND 2000 "
            "AND public_feedback IS NULL AND measure IS NULL) "
            "OR (action = 'replied' AND public_feedback IS NOT NULL "
            "AND char_length(public_feedback) BETWEEN 1 AND 2000 "
            "AND internal_note IS NULL AND measure IS NULL) "
            "OR (action = 'resolved' AND public_feedback IS NOT NULL "
            "AND char_length(public_feedback) BETWEEN 1 AND 2000 "
            "AND measure IS NOT NULL AND char_length(measure) BETWEEN 1 AND 500 "
            "AND internal_note IS NULL)",
            name="ck_report_events_payload",
        ),
        Index("ix_report_events_report", "report_id", "created_at"),
        Index(
            "uq_report_events_resolved",
            "report_id",
            unique=True,
            postgresql_where=text("action = 'resolved'"),
        ),
    )
    return reports, events
