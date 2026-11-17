"""文档、作业与原子版本发布。所有向量写入使用参数化 SQL。"""
import hashlib
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import (
    bindparam,
    cast,
    delete,
    func,
    insert,
    literal,
    select,
    true,
    update,
)

from melonclaw.core.knowledge import (
    KnowledgeConflict,
    KnowledgeDenied,
    KnowledgeError,
    require_role,
)
from melonclaw.core.knowledge_ocr import FILE_PARTS
from melonclaw.database.knowledge_schema import KnowledgeVector
from melonclaw.database.schema import KNOWLEDGE_TABLES, users
from melonclaw.repository.knowledge import visible_query
from melonclaw.repository.mappers import _now

bases, members, documents, revisions, texts, chunks, jobs, events = KNOWLEDGE_TABLES


def request_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def upload_digest(kb_id, document_id, name, source_hash, spec, *, replace):
    return request_digest([str(kb_id), str(document_id) if replace else None, name, source_hash, spec])


class KnowledgeDocumentsRepositoryMixin:
    async def knowledge_upload(self, user_id, kb_id, *, document_id, revision_id, name,
                               source_hash, source_byte_size, mime_type, request_id, processing_spec,
                               replace=False):
        operation = "replace" if replace else "upload"
        async with self.knowledge_transaction(user_id, kb_id) as (conn, base):
            require_role(base["role"], "documents")
            # 分块配方在幂等哈希里冻结；同一请求不能在改过配置后悄悄复用旧作业。
            spec = {**processing_spec, "chunk_config": base["chunk_config"]}
            digest = upload_digest(kb_id, document_id, name, source_hash, spec, replace=replace)
            existing = (await conn.execute(select(jobs).where(
                jobs.c.requested_by == user_id, jobs.c.operation == operation,
                jobs.c.request_id == request_id,
            ))).mappings().first()
            if existing:
                if existing["request_hash"] != digest:
                    raise KnowledgeConflict("同一请求标识不能用于不同文件或配置。")
                return {key: existing[key] for key in ("id", "document_id", "revision_id", "status")}
            self._knowledge_ingestion_available(base)
            if replace:
                doc = (await conn.execute(select(documents).where(
                    documents.c.kb_id == kb_id, documents.c.id == document_id,
                    documents.c.deleted_at.is_(None),
                ).with_for_update())).mappings().first()
                if not doc:
                    raise KnowledgeDenied("文档不存在或无权访问。")
                active = await conn.scalar(select(jobs.c.id).where(
                    jobs.c.document_id == document_id, jobs.c.status.in_(["queued", "running"])))
                if active:
                    raise KnowledgeConflict("该文档仍有待处理作业。")
                number = (await conn.scalar(select(func.max(revisions.c.revision_no)).where(
                    revisions.c.document_id == document_id))) + 1
            else:
                number = 1
                await conn.execute(insert(documents).values(
                    id=document_id, kb_id=kb_id, display_name=name, created_by=user_id,
                    created_at=_now(), updated_at=_now(),
                ))
            await conn.execute(insert(revisions).values(
                id=revision_id, kb_id=kb_id, document_id=document_id, revision_no=number,
                source_key=f"{kb_id}/{document_id}/{revision_id}/source", source_hash=source_hash,
                source_byte_size=source_byte_size, mime_type=mime_type, processing_spec=spec,
                created_at=_now(),
            ))
            job_id = uuid4()
            await conn.execute(insert(jobs).values(
                id=job_id, kb_id=kb_id, document_id=document_id, revision_id=revision_id,
                operation=operation, requested_by=user_id, request_id=request_id,
                request_hash=digest, status="queued", created_at=_now(), updated_at=_now(),
            ))
            await conn.execute(update(bases).where(bases.c.id == kb_id).values(active_pipeline_job_id=job_id))
            return {"id": job_id, "document_id": document_id, "revision_id": revision_id, "status": "queued"}

    async def knowledge_documents(self, user_id, kb_id, *, page=1, page_size=20):
        async with self.knowledge_transaction(user_id, kb_id):
            pass
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            # 每条查询本身含 ACL，不依赖前面释放后的校验。
            condition = [documents.c.kb_id == kb_id, documents.c.deleted_at.is_(None),
                         documents.c.kb_id.in_(select(visible_query(user_id).subquery().c.id))]
            total = await conn.scalar(select(func.count()).select_from(documents).where(*condition))
            rows = (await conn.execute(select(documents).where(*condition).order_by(
                documents.c.updated_at.desc(), documents.c.id).offset((page-1)*page_size).limit(page_size))).mappings().all()
            document_ids = [row["id"] for row in rows]
            grouped = {}
            if document_ids:
                # 每份文档只读取最新资料处理作业。
                selected = select(documents.c.id).where(documents.c.id.in_(document_ids)).subquery()

                def latest_query(operations, category):
                    latest = select(
                        jobs.c.id, jobs.c.document_id, jobs.c.operation, jobs.c.status, jobs.c.stage,
                        jobs.c.error_code, jobs.c.error_summary, jobs.c.created_at,
                    ).where(jobs.c.kb_id == kb_id, jobs.c.document_id == selected.c.id,
                            jobs.c.operation.in_(operations)).order_by(
                        jobs.c.created_at.desc(), jobs.c.id.desc()).limit(1).lateral()
                    return select(latest, literal(category).label("category")).select_from(selected.join(latest, true()))

                job_rows = (await conn.execute(
                    latest_query(["upload", "replace", "reparse", "rechunk"], "source_job")
                )).mappings()
                for job in job_rows:
                    grouped.setdefault(job["document_id"], {})[job["category"]] = {
                        key: value for key, value in job.items() if key != "category"
                    }

            items = [{**dict(row), "source_job": grouped.get(row["id"], {}).get("source_job")} for row in rows]
            active = await conn.scalar(select(bases.c.active_pipeline_job_id).where(
                bases.c.id == kb_id, bases.c.id.in_(select(visible_query(user_id).subquery().c.id))))
            return {"items": items, "total": total, "page": page, "page_size": page_size,
                    "active_pipeline_job_id": active}

    async def knowledge_source(self, user_id, kb_id, document_id):
        async with self.knowledge_transaction(user_id, kb_id) as (conn, _):
            doc = (await conn.execute(select(documents).where(
                documents.c.id == document_id, documents.c.kb_id == kb_id,
                documents.c.deleted_at.is_(None)))).mappings().first()
            if not doc or not doc["active_revision_id"]:
                raise KnowledgeDenied("文档尚无可用版本。")
            revision = (await conn.execute(select(revisions).where(
                revisions.c.id == doc["active_revision_id"], revisions.c.kb_id == kb_id))).mappings().one()
            return {**dict(revision), "display_name": doc["display_name"]}

    async def knowledge_document_action(self, user_id, kb_id, document_id, action, request_id=None):
        async with self.knowledge_transaction(user_id, kb_id) as (conn, base):
            require_role(base["role"], "documents")
            doc = (await conn.execute(select(documents).where(
                documents.c.id == document_id, documents.c.kb_id == kb_id,
                documents.c.deleted_at.is_(None)).with_for_update())).mappings().first()
            if not doc:
                raise KnowledgeDenied("文档不存在或无权访问。")
            revs = (await conn.execute(select(revisions).where(revisions.c.document_id == document_id)
                                      .order_by(revisions.c.id).with_for_update())).mappings().all()
            job_rows = (await conn.execute(select(jobs).where(jobs.c.document_id == document_id)
                                          .order_by(jobs.c.id).with_for_update())).mappings().all()
            active = [j for j in job_rows if j["status"] in {"queued", "running"}]
            if action == "delete" and base["active_pipeline_job_id"] is not None:
                raise KnowledgeConflict("请先结束当前资料处理，等待实际退出后再删除。")
            if action in {"delete", "cancel"}:
                if active:
                    await conn.execute(update(jobs).where(jobs.c.id.in_([j["id"] for j in active])).values(
                        status="cancelled", run_token=None, updated_at=_now(), finished_at=_now()))
                queued_ids = [j["id"] for j in active if j["status"] == "queued"]
                if queued_ids:
                    await conn.execute(update(bases).where(bases.c.id == kb_id,
                        bases.c.active_pipeline_job_id.in_(queued_ids)).values(active_pipeline_job_id=None))
                if action == "delete":
                    await conn.execute(update(documents).where(documents.c.id == document_id).values(
                        deleted_at=_now(), updated_at=_now()))
                    await self._knowledge_event(conn, kb_id, user_id, "document_deleted", "document", document_id)
                return {"ok": True}
            if action != "retry" or not request_id:
                raise KnowledgeError("文档操作无效。")
            previous = max(job_rows, key=lambda j: (j["created_at"], str(j["id"])), default=None)
            if previous and previous["request_id"] == request_id:
                return {"id": previous["id"], "status": previous["status"]}
            if active or not previous or previous["status"] not in {"failed", "cancelled"}:
                raise KnowledgeConflict("只能重试已失败或取消且实际退出的作业。")
            self._knowledge_ingestion_available(base)
            revision = next(r for r in revs if r["id"] == previous["revision_id"])
            if revision["published_at"]:
                raise KnowledgeConflict("已发布版本不可改写。")
            reused = await conn.scalar(select(jobs.c.id).where(jobs.c.requested_by == user_id,
                jobs.c.operation == previous["operation"], jobs.c.request_id == request_id))
            if reused:
                raise KnowledgeConflict("请求标识已使用。")
            job_id = uuid4()
            await conn.execute(insert(jobs).values(
                id=job_id, kb_id=kb_id, document_id=document_id, revision_id=revision["id"],
                requested_by=user_id, request_id=request_id, request_hash=previous["request_hash"],
                operation=previous["operation"], status="queued", created_at=_now(), updated_at=_now()))
            await conn.execute(update(bases).where(bases.c.id == kb_id).values(active_pipeline_job_id=job_id))
            return {"id": job_id, "status": "queued"}

    async def knowledge_queue(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(jobs).where(jobs.c.status == "queued")
                                      .order_by(jobs.c.created_at, jobs.c.id).limit(100))
            return [dict(r) for r in rows.mappings()]

    @asynccontextmanager
    async def knowledge_job_transaction(self, job):
        # 允许在账户已失效时安全收敛失败；发布仍需额外实时 ACL。
        async with self.engine.begin() as conn:
            owner = await conn.scalar(select(bases.c.owner_user_id).where(bases.c.id == job["kb_id"]))
            await conn.execute(select(users.c.user_id).where(users.c.user_id.in_(
                sorted({job["requested_by"], owner}))).order_by(users.c.user_id).with_for_update())
            base = (await conn.execute(select(bases).where(bases.c.id == job["kb_id"])
                                       .with_for_update())).mappings().one()
            if base["owner_user_id"] != owner:
                raise KnowledgeConflict("所有权变化，作业需重试。")
            if job["document_id"] is None:
                row = (await conn.execute(select(jobs).where(jobs.c.id == job["id"]).with_for_update())).mappings().one()
                yield conn, base, None, None, row
                return
            doc = (await conn.execute(select(documents).where(documents.c.id == job["document_id"])
                                      .with_for_update())).mappings().one()
            locked_revisions = (await conn.execute(select(revisions).where(
                revisions.c.document_id == job["document_id"]).order_by(revisions.c.id)
                .with_for_update())).mappings().all()
            revision = next(r for r in locked_revisions if r["id"] == job["revision_id"])
            row = (await conn.execute(select(jobs).where(jobs.c.id == job["id"])
                                      .with_for_update())).mappings().one()
            yield conn, base, doc, revision, row

    async def knowledge_claim(self, job):
        async with self.knowledge_job_transaction(job) as (conn, base, doc, revision, row):
            if row["status"] != "queued":
                return None
            if base["active_pipeline_job_id"] != row["id"]:
                raise KnowledgeConflict("知识库流程占用不一致。")
            visible = (await conn.execute(visible_query(job["requested_by"]).where(
                bases.c.id == job["kb_id"]))).mappings().first()
            if not visible or visible["role"] == "viewer" or doc["deleted_at"]:
                await conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                    status="cancelled", run_token=None, finished_at=_now(), updated_at=_now()))
                await conn.execute(update(bases).where(bases.c.id == base["id"],
                    bases.c.active_pipeline_job_id == row["id"]).values(active_pipeline_job_id=None))
                return None
            token = uuid4()
            await conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(
                status="running", stage="parsing", run_token=token, started_at=_now(), updated_at=_now()))
            return {**dict(row), "run_token": token, "revision": dict(revision),
                    "embedding_model_id": base["embedding_model_id"], "knowledge_mode": base["knowledge_mode"]}

    async def knowledge_job_check(self, job, stage=None):
        if stage:
            job["stage"] = stage
        async with self.knowledge_job_transaction(job) as (conn, base, doc, revision, row):
            if row["status"] != "running" or row["run_token"] != job["run_token"]:
                raise KnowledgeConflict("作业已终止。")
            visible = (await conn.execute(visible_query(job["requested_by"]).where(
                bases.c.id == job["kb_id"]))).mappings().first()
            if not visible or visible["role"] == "viewer" or doc["deleted_at"] or revision["published_at"]:
                raise KnowledgeDenied("作业授权已失效。")
            if stage:
                await conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(stage=stage, updated_at=_now()))

    async def knowledge_finish_failure(self, job, code="processing_failed", summary=None):
        async with self.knowledge_job_transaction(job) as (conn, _, _, revision, row):
            if row["status"] not in {"queued", "running"} or row["run_token"] != job.get("run_token"):
                return
            summary = summary or "处理未完成，请稍后重试。"
            if (row["stage"] in {"chunking", "embedding", "publishing"} and revision is not None
                    and revision["processing_spec"].get("extension") in FILE_PARTS):
                summary += "\n重试原文会重新识别，可能再次计费。"
            await conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(
                status="failed", run_token=None, finished_at=_now(), updated_at=_now(),
                error_code=code[:64],
                error_summary=summary[:1000]))

    async def knowledge_publish(self, job, content, records):
        # 应用侧再次验证原样切片；调用者不能用任意正文/向量绕过约束。
        if not content or any(content[r["start_offset"]:r["end_offset"]] != r["content"] for r in records):
            raise KnowledgeError("分块内容无效。")
        async with self.knowledge_job_transaction(job) as (conn, base, doc, revision, row):
            visible = (await conn.execute(visible_query(job["requested_by"]).where(
                bases.c.id == job["kb_id"]))).mappings().first()
            if (row["status"] != "running" or row["run_token"] != job["run_token"]
                or not visible or visible["role"] == "viewer" or doc["deleted_at"] or revision["published_at"]):
                raise KnowledgeConflict("作业已失效，不能发布。")
            if not records:
                raise KnowledgeError("分块内容无效。")
            await self.assert_fixed_embedding_reference(conn, base["embedding_model_id"])
            await conn.execute(delete(chunks).where(chunks.c.revision_id == revision["id"]))
            await conn.execute(delete(texts).where(texts.c.revision_id == revision["id"]))
            await conn.execute(insert(texts).values(
                kb_id=base["id"], revision_id=revision["id"], content=content,
                text_hash=hashlib.sha256(content.encode()).hexdigest(), char_count=len(content)))
            payload = [{**r, "id": uuid4(), "kb_id": base["id"], "revision_id": revision["id"]} for r in records]
            if payload:
                await conn.execute(insert(chunks).values(embedding=cast(bindparam("vector"), KnowledgeVector())), payload)
            now = _now()
            if doc["active_revision_id"]:
                await conn.execute(update(revisions).where(revisions.c.id == doc["active_revision_id"])
                                   .values(superseded_at=now))
            await conn.execute(update(revisions).where(revisions.c.id == revision["id"]).values(published_at=now))
            await conn.execute(update(documents).where(documents.c.id == doc["id"]).values(
                active_revision_id=revision["id"], updated_at=now))
            await conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(
                status="succeeded", stage="publishing", run_token=None, finished_at=now, updated_at=now))
            await self._knowledge_event(conn, base["id"], job["requested_by"], "revision_published", "revision", revision["id"])

    async def knowledge_recover(self):
        # 仅持有单实例执行器会话锁的启动方调用，逐个按统一锁顺序收敛旧运行。
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(jobs).where(jobs.c.status == "running"))).mappings().all()
        for row in rows:
            await self.knowledge_finish_failure(dict(row), "service_restarted", "服务已重启，处理未完成，请重试。")
        async with self.engine.begin() as conn:
            await conn.execute(update(jobs).where(jobs.c.started_at.is_not(None), jobs.c.status.not_in(["queued", "running"])).values(execution_exited=True))
        async with self.engine.connect() as conn:
            occupied = (await conn.execute(select(jobs).where(jobs.c.id.in_(select(bases.c.active_pipeline_job_id))))).mappings().all()
        for row in occupied:
            await self.knowledge_release_pipeline(dict(row))


    @staticmethod
    def _knowledge_ingestion_available(base):
        if base["active_pipeline_job_id"] is not None:
            raise KnowledgeConflict("当前知识库仍在处理一份资料，请等待实际结束。")

    async def knowledge_release_pipeline(self, job):
        """只在执行协程实际退出后调用；运行中取消不提前释放数据库名额。"""
        async with self.knowledge_job_transaction(job) as (conn, base, _doc, _revision, row):
            await conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(execution_exited=True))
            if row["status"] not in {"queued", "running"}:
                await conn.execute(update(bases).where(bases.c.id == base["id"],
                    bases.c.active_pipeline_job_id == row["id"]).values(active_pipeline_job_id=None))
