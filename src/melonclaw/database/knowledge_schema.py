"""知识库表定义；由 schema.py 注册到统一 metadata。"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.types import UserDefinedType


class KnowledgeVector(UserDefinedType):
    """仅声明 PostgreSQL DDL 类型；运行时向量适配在检索实现阶段接入。"""
    cache_ok = True
    dimensions = 1024

    def get_col_spec(self, **kw):
        return f"VECTOR({self.dimensions})"


def register_knowledge_tables(metadata):
    def col(name, typ, nullable=False, **kw):
        return Column(name, typ, nullable=nullable, **kw)

    def uid(name="id", **kw):
        return col(name, UUID(as_uuid=True), **kw)

    def stamp(name, nullable=False):
        return col(name, DateTime(timezone=True), nullable)

    def ref(name, target, typ=None, nullable=False):
        return Column(name, typ or UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)

    def user(name, nullable=False):
        return ref(name, "users.user_id", String(64), nullable)

    def kb():
        return ref("kb_id", "knowledge_bases.id")

    def check(expr, name):
        return CheckConstraint(expr, name="ck_kb_" + name)

    def obj(name):
        return check(f"jsonb_typeof({name}) = 'object'", name + "_object")

    def table(name, *items):
        return Table("knowledge_" + name, metadata, *items)

    bases = table("bases", uid(primary_key=True), user("owner_user_id"),
        col("name", String(255)), col("description", Text, server_default=""),
        col("status", String(16), server_default="active"),
        col("share_scope", String(16), server_default="selected"),
        ref("embedding_model_id", "model_configs.id"),
        ref("parser_model_id", "model_configs.id", nullable=True),
        col("knowledge_mode", String(16), server_default="vector"),
        uid("active_pipeline_job_id", nullable=True),
        col("chunk_config", JSONB(none_as_null=True)), col("version", Integer, server_default="1"),
        stamp("created_at"), stamp("updated_at"), stamp("deleted_at", True),
        check("status IN ('active','suspended','deleted')", "base_status"),
        check("share_scope IN ('everyone','tenant','selected')", "base_share"),
        check("(status = 'deleted') = (deleted_at IS NOT NULL)", "base_deleted"),
        check("version > 0 AND length(btrim(name)) > 0", "base_values"),
        check("updated_at >= created_at AND (deleted_at IS NULL OR deleted_at >= created_at)", "base_times"),
        obj("chunk_config"),
        check("knowledge_mode = 'vector'", "base_mode"))
    members = table("members", kb(), user("user_id"), col("role", String(16)),
        user("granted_by"), stamp("created_at"), stamp("updated_at"),
        check("role IN ('viewer','editor')", "member_role"),
        check("updated_at >= created_at", "member_times"))
    from sqlalchemy import PrimaryKeyConstraint
    members.append_constraint(PrimaryKeyConstraint("kb_id", "user_id"))
    documents = table("documents", uid(primary_key=True), kb(),
        col("display_name", String(255)), uid("active_revision_id", nullable=True),
        user("created_by"), stamp("created_at"), stamp("updated_at"), stamp("deleted_at", True),
        UniqueConstraint("kb_id", "id", name="uq_kb_document"),
        check("length(btrim(display_name)) > 0", "document_name"),
        check("updated_at >= created_at AND (deleted_at IS NULL OR deleted_at >= created_at)", "document_times"))
    revisions = table("document_revisions", uid(primary_key=True), kb(), uid("document_id"),
        col("revision_no", Integer), col("source_key", Text), col("source_hash", String(64)),
        col("source_byte_size", BigInteger), col("mime_type", String(255)),
        col("processing_spec", JSONB), stamp("created_at"), stamp("published_at", True), stamp("superseded_at", True),
        UniqueConstraint("kb_id", "document_id", "id", name="uq_kb_revision_document"),
        UniqueConstraint("kb_id", "id", name="uq_kb_revision"),
        UniqueConstraint("document_id", "revision_no", name="uq_kb_revision_number"),
        ForeignKeyConstraint(["kb_id", "document_id"], ["knowledge_documents.kb_id", "knowledge_documents.id"], ondelete="RESTRICT"),
        check("revision_no > 0 AND source_byte_size >= 0 AND length(source_key) > 0", "revision_values"),
        check("source_hash ~ '^[0-9a-f]{64}$'", "source_hash"), obj("processing_spec"),
        check("published_at IS NULL OR published_at >= created_at", "published_time"),
        check("superseded_at IS NULL OR (published_at IS NOT NULL AND superseded_at >= published_at)", "superseded_time"))
    documents.append_constraint(ForeignKeyConstraint(
        ["kb_id", "id", "active_revision_id"],
        ["knowledge_document_revisions.kb_id", "knowledge_document_revisions.document_id", "knowledge_document_revisions.id"],
        name="fk_kb_document_active_revision", use_alter=True, ondelete="RESTRICT"))

    def revision_fk():
        return ForeignKeyConstraint(["kb_id", "revision_id"],
            ["knowledge_document_revisions.kb_id", "knowledge_document_revisions.id"], ondelete="RESTRICT")

    texts = table("document_texts", uid("revision_id", primary_key=True), kb(),
        col("content", Text), col("text_hash", String(64)), col("char_count", Integer), revision_fk(),
        check("char_count > 0 AND char_count = char_length(content)", "text_length"),
        check("text_hash ~ '^[0-9a-f]{64}$'", "text_hash"))
    chunks = table("chunks", uid(primary_key=True), kb(), uid("revision_id"),
        col("ordinal", Integer), col("content", Text), col("start_offset", Integer), col("end_offset", Integer),
        col("source_locator", JSONB, True), col("search_text", Text),
        Column("search_vector", TSVECTOR, Computed("to_tsvector('pg_catalog.simple'::regconfig, search_text)", persisted=True), nullable=False),
        col("embedding", KnowledgeVector()), revision_fk(),
        UniqueConstraint("revision_id", "ordinal", name="uq_kb_chunk_ordinal"),
        check("ordinal >= 0 AND start_offset >= 0 AND end_offset > start_offset AND char_length(content) = end_offset - start_offset", "chunk_range"),
        check("vector_norm(embedding) > 0", "vector_nonzero"), obj("source_locator"))
    jobs = table("jobs", uid(primary_key=True), kb(), uid("document_id", nullable=True), uid("revision_id", nullable=True),
        col("execution_exited", Boolean, server_default=text("false")),
        col("operation", String(16)), user("requested_by"), col("request_id", String(128)), col("request_hash", String(64)),
        col("status", String(16), server_default="queued"), col("stage", String(16), True), uid("run_token", nullable=True),
        col("error_code", String(64), True), col("error_summary", String(1000), True),
        stamp("created_at"), stamp("updated_at"), stamp("started_at", True), stamp("finished_at", True),
        ForeignKeyConstraint(["kb_id", "document_id"], ["knowledge_documents.kb_id", "knowledge_documents.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["kb_id", "document_id", "revision_id"], ["knowledge_document_revisions.kb_id", "knowledge_document_revisions.document_id", "knowledge_document_revisions.id"], ondelete="RESTRICT"),
        UniqueConstraint("requested_by", "operation", "request_id", name="uq_kb_job_request"),
        UniqueConstraint("kb_id", "id", name="uq_kb_job_base"),
        check("operation IN ('upload','replace','reparse','rechunk')", "job_operation"),
        check("length(btrim(request_id)) > 0 AND request_hash ~ '^[0-9a-f]{64}$'", "job_request"),
        check("stage IS NULL OR stage IN ('parsing','chunking','embedding','publishing')", "job_stage"),
        check("revision_id IS NULL OR document_id IS NOT NULL", "job_target"),
        check("status IN ('succeeded','failed','cancelled') OR (document_id IS NOT NULL AND revision_id IS NOT NULL)", "job_live_target"),
        check("(status = 'queued' AND started_at IS NULL AND finished_at IS NULL AND run_token IS NULL AND stage IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL AND run_token IS NOT NULL AND stage IS NOT NULL) OR (status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND run_token IS NULL AND error_code IS NULL AND error_summary IS NULL) OR (status = 'failed' AND finished_at IS NOT NULL AND run_token IS NULL AND error_code IS NOT NULL) OR (status = 'cancelled' AND finished_at IS NOT NULL AND run_token IS NULL)", "job_state"),
        check("error_summary IS NULL OR error_code IS NOT NULL", "job_error"),
        check("status NOT IN ('queued','running') OR (error_code IS NULL AND error_summary IS NULL)", "job_live_error"),
        check("updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) AND (finished_at IS NULL OR (finished_at >= created_at AND (started_at IS NULL OR finished_at >= started_at)))", "job_times"))
    bases.append_constraint(ForeignKeyConstraint(
        ["id", "active_pipeline_job_id"], ["knowledge_jobs.kb_id", "knowledge_jobs.id"],
        name="fk_kb_active_pipeline", use_alter=True, ondelete="RESTRICT"))
    Index("ux_knowledge_jobs_active_base", jobs.c.kb_id, unique=True,
          postgresql_where=jobs.c.status.in_(["queued", "running"]))
    events = table("events", uid(primary_key=True), kb(), user("actor_user_id", True),
        col("action", String(32)), col("target_type", String(16)), col("target_id", String(128)),
        col("safe_details", JSONB, server_default=text("'{}'::jsonb")), stamp("created_at"), obj("safe_details"),
        check("action IN ('base_created','base_deleted','base_updated','ownership_transferred','share_scope_changed','member_granted','member_updated','member_revoked','document_deleted','revision_published')", "event_action"),
        check("target_type IN ('base','member','document','revision','job') AND length(target_id) > 0", "event_target"))
    for t, suffix, cols in [
        (bases,"owner",["owner_user_id","status"]), (bases,"embedding_model",["embedding_model_id"]),
        (bases,"parser_model",["parser_model_id"]),
        (members,"user",["user_id","kb_id"]),
        (chunks,"revision",["kb_id","revision_id"]), (texts,"revision",["kb_id","revision_id"]),
        (jobs,"queue",["status","created_at","id"]), (jobs,"list",["kb_id","created_at","id"]),
        (jobs,"revision",["revision_id","created_at","id"]), (jobs,"document",["document_id","created_at","id"]),
        (events,"list",["kb_id","created_at","id"]), (revisions,"source",["kb_id","source_key"]),
    ]:
        Index("ix_" + t.name + "_" + suffix, *(t.c[c] for c in cols))
    Index("ix_knowledge_documents_list", documents.c.kb_id, documents.c.updated_at, documents.c.id, postgresql_where=documents.c.deleted_at.is_(None))
    for t, field in [(documents,"deleted_at"),(revisions,"superseded_at"),(jobs,"finished_at")]:
        Index("ix_" + t.name + "_cleanup", t.c[field], postgresql_where=t.c[field].is_not(None))
    Index("ux_knowledge_jobs_active", jobs.c.document_id, unique=True, postgresql_where=jobs.c.status.in_(["queued","running"]))
    Index("ix_knowledge_chunks_search", chunks.c.search_vector, postgresql_using="gin")
    return (bases, members, documents, revisions, texts, chunks, jobs, events)


SEARCH_VIEW_SQL = """
CREATE OR REPLACE VIEW knowledge_search_chunks AS
SELECT c.*, r.document_id, b.embedding_model_id, u.tenant_id
FROM knowledge_chunks c
JOIN knowledge_document_revisions r ON r.id=c.revision_id AND r.kb_id=c.kb_id
JOIN knowledge_documents d ON d.id=r.document_id AND d.kb_id=r.kb_id
JOIN knowledge_bases b ON b.id=c.kb_id
JOIN users u ON u.user_id=b.owner_user_id
WHERE b.status='active' AND d.deleted_at IS NULL
AND r.published_at IS NOT NULL AND d.active_revision_id=r.id
"""
