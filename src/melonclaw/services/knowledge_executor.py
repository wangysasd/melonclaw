"""单 Web 内置知识库执行器；占用持续到实际协程/子进程退出。"""
import asyncio
import logging
from uuid import UUID

from sqlalchemy import text

from melonclaw.core.knowledge import KnowledgeError
from melonclaw.services.knowledge_processing import KnowledgeProcessingError, KnowledgeProcessor

logger = logging.getLogger(__name__)
_STAGE_SUMMARY = {
    "parsing": "文档解析失败，请检查文件后重试。",
    "chunking": "文档分块失败。若调整了分块设置，请替换或重新上传文件以应用新设置。",
    "embedding": "向量化失败。请联系维护人员检查嵌入服务后重试。",
    "publishing": "发布失败，请稍后重试。",

}


def failure_report(job, exc):
    """把处理异常收成固定错误码和阶段摘要，不携带异常原文。"""

    if isinstance(exc, asyncio.CancelledError):
        return "service_shutdown", "服务已停止，处理未完成，请重试。"
    if isinstance(exc, KnowledgeProcessingError):
        return exc.code, exc.summary
    if isinstance(exc, TimeoutError):
        return "processing_timeout", "处理超过时限，请稍后重试。"
    stage = job.get("stage") or "parsing"
    return "processing_failed", _STAGE_SUMMARY.get(stage, "处理未完成，请重试。")

EXECUTOR_ID = UUID("de47827d-a579-4210-a13c-2828f373d134")


class KnowledgeExecutor:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        self.processor = KnowledgeProcessor(settings, repository)
        self.connection = None
        self.loop_task = None
        self.active = {}
        self.ready = False

    async def start(self):
        self.connection = await self.repository.try_advisory_lock(EXECUTOR_ID)
        if self.connection is None:
            raise RuntimeError("知识库执行器已被其他实例占用。")
        try:
            await self.repository.knowledge_recover()
            self.ready = True
            self.loop_task = asyncio.create_task(self.loop())
        except BaseException:
            await self.repository.release_advisory_lock(self.connection, EXECUTOR_ID)
            self.connection = None
            raise

    async def loop(self):
        try:
            while True:
                if not self.ready:
                    raise RuntimeError("知识库执行器已失去就绪状态。")
                await self.connection.execute(text("SELECT 1"))
                await self.connection.commit()
                for queued in await self.repository.knowledge_queue():
                    if not self.runnable(queued, self.active.values()):
                        continue
                    job = await self.claim_queued(queued)
                    if not job:
                        continue
                    task = asyncio.create_task(self.execute(job))
                    self.active[job["id"]] = {**job, "task": task}
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 会话锁连接丢失不自动重连，不再领取或发布本地作业。
            self.ready = False
            for value in list(self.active.values()):
                value["task"].cancel()

    def runnable(self, queued, active):
        """同一库、同一文档不并行；同一个人的其他库可以同时处理。"""

        if len(active) >= self.settings.knowledge_concurrency:
            return False
        document_id = queued.get("document_id")
        kb_id = queued.get("kb_id")
        if document_id and any(item.get("document_id") == document_id for item in active):
            return False
        if kb_id and any(item.get("kb_id") == kb_id for item in active):
            return False
        return True

    async def claim_queued(self, queued):
        """领取失败只结束这一条作业。连接中断仍由轮询循环停止执行器。"""
        try:
            return await self.repository.knowledge_claim(queued)
        except KnowledgeError:
            try:
                await self.repository.knowledge_finish_failure(
                    queued, "processing_failed", "作业无法开始，流程状态不一致。")
            except Exception:
                self.ready = False
                raise
            return None

    async def execute(self, job):
        process = asyncio.create_task(self.processor.run(job))
        try:
            async with asyncio.timeout(self.settings.knowledge_job_timeout_seconds):
                while not process.done():
                    await asyncio.wait({process}, timeout=0.5)
                    if not process.done():
                        await self.repository.knowledge_job_check(job)
                await process
        except BaseException as exc:
            process.cancel()
            await asyncio.gather(process, return_exceptions=True)
            code, summary = failure_report(job, exc)
            # 只记录类型、作业和阶段，避免正文、路径或供应商响应进入日志。
            logger.log(logging.INFO if code == "service_shutdown" else logging.WARNING,
                       "knowledge job failed job=%s operation=%s stage=%s code=%s error_type=%s",
                       job.get("id"), job.get("operation"), job.get("stage"), code, type(exc).__name__)
            try:
                await self.repository.knowledge_finish_failure(job, code, summary)
            except Exception:
                logger.warning("knowledge job failure could not be recorded job=%s error_type=%s",
                               job.get("id"), type(exc).__name__)
                self.ready = False
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            # 数据库 cancelled 不提前触发这里；必须等 process 的 finally 收尾。
            try:
                await self.repository.knowledge_release_pipeline(job)
            except Exception:
                self.ready = False
            finally:
                self.active.pop(job["id"], None)

    async def close(self):
        self.ready = False
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        tasks = [v["task"] for v in self.active.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.connection is not None:
            try:
                await self.repository.release_advisory_lock(self.connection, EXECUTOR_ID)
            finally:
                self.connection = None
