"""Web 生命周期内的 APScheduler 周期扫描与有界异步执行。"""

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.task_agent import TaskAgentOptions
from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_policy import TaskExecutionError, TaskRunControl
from melonclaw.core.tracing import Tracing
from melonclaw.repository.task_history import load_history
from melonclaw.repository.task_queue import TaskQueueRepository
from melonclaw.services.task_execution import TaskOutcome, TaskProgress, execute_task_agent
from melonclaw.services.task_history import RunHistoryProvider, prepare_history
from melonclaw.services.tasks import TaskService

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, settings, storage, checkpointer, *, tracing: Tracing | None = None):
        self.tracing = tracing
        self.settings = settings
        self.service = TaskService(settings, storage)
        self.queue = TaskQueueRepository(storage.engine)
        self.checkpointer = checkpointer
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self.jobs = set()
        self.scan_lock = asyncio.Lock()
        self.stopping = False
        self.started = False
        self.last_scan_at = None
        self.error = None
        self.next_cleanup = 0.0

    def status(self):
        return {
            "ready": self.started and not self.stopping and self.error is None,
            "running": len(self.jobs),
            "last_scan_at": self.last_scan_at,
            "error": self.error,
        }

    async def start(self):
        try:
            await self.queue.open()
            await self.queue.interrupt_running("service_restarted")
            self.scheduler.add_job(
                self.tick,
                "interval",
                seconds=self.settings.task_poll_seconds,
                id="task-scan",
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(UTC),
            )
            self.scheduler.start()
            self.started = True
        except BaseException:
            await self.queue.close()
            raise

    async def close(self):
        self.stopping = True
        self.started = False
        if self.scheduler.running:
            self.scheduler.pause()
        async with self.scan_lock:
            jobs = tuple(self.jobs)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            if self.queue.connection is not None:
                try:
                    # 协程可能在首次运行前就被取消；它来不及自行写终态。
                    await self.queue.interrupt_running("service_shutdown")
                except Exception:
                    logger.warning("任务停服状态未提交，重启时将按中断处理。")
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                await asyncio.sleep(0)
            await self.queue.close()

    async def tick(self):
        async with self.scan_lock:
            if self.stopping:
                return
            try:
                await self.queue.expire_queued(self.settings.task_queue_timeout_seconds)
                await self.service.dispatch_due()
                loop = asyncio.get_running_loop()
                if loop.time() >= self.next_cleanup:
                    try:
                        await self.service.cleanup_checkpoints(self.checkpointer)
                    except Exception:
                        logger.warning("任务保留数据清理未完成，下轮重试。")
                    self.next_cleanup = loop.time() + self.settings.task_cleanup_interval_seconds
                while not self.stopping and len(self.jobs) < self.settings.task_global_concurrency:
                    run = await self.queue.claim(
                        global_limit=self.settings.task_global_concurrency,
                        user_limit=self.settings.task_user_concurrency,
                    )
                    if run is None:
                        break
                    job = asyncio.create_task(self.execute(run))
                    self.jobs.add(job)
                    job.add_done_callback(self.jobs.discard)
                self.last_scan_at = datetime.now(UTC)
            except Exception:
                # 失去数据库会话后不能让旧执行继续，也不自动重连并接管新实例的运行。
                self.error = "任务调度已停止，请检查数据库并重启 Web。"
                self.started = False
                self.stopping = True
                self.scheduler.pause()
                jobs = tuple(self.jobs)
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                await self.queue.close()
                logger.warning(self.error)

    async def execute(self, run):
        logger.info(
            "task_id=%s run_id=%s status=running",
            run["task_id"],
            run["id"],
        )
        settings = self.settings
        control = TaskRunControl(settings.agent_model_call_limit, settings.agent_tool_call_limit)
        progress = TaskProgress(settings.task_result_max_chars)
        execution = None
        owned = True
        user_cancelled = False
        limits = dict(max_chars=settings.task_result_max_chars)
        last_saved_text = progress.text

        async def invoke(deadline):
            user, definition, model = await self.service.execution_input(run)
            if self.tracing is None:
                return await invoke_agent(user, definition, model)
            with self.tracing.run(
                feature="task-run", user_id=user.user_id, tenant_id=user.tenant_id,
                conversation_id=str(run["config_snapshot"]["history"]["conversation_id"]), run_id=str(run["id"]),
                input=definition.instruction,
                metadata={"task_id": str(run["task_id"]), "model_id": definition.model_id},
            ) as trace:
                try:
                    outcome = await invoke_agent(user, definition, model)
                except asyncio.CancelledError:
                    trace.finish(
                        output=progress.text,
                        status="cancelled" if user_cancelled else "failed",
                        error_code="execution_timeout" if deadline.expired()
                        else "cancelled" if user_cancelled else "service_shutdown",
                    )
                    raise
                if deadline.expired() or outcome.status == "cancelled" and not user_cancelled:
                    trace.finish(output=outcome.result_markdown, status="failed",
                                 error_code="execution_timeout" if deadline.expired() else "service_shutdown")
                else:
                    trace.finish(output=outcome.result_markdown, status=outcome.status,
                                 error_code=outcome.error_code)
                return outcome

        async def invoke_agent(user, definition, model):
            await self.queue.save_model_display_name(run, model.display_name)
            async with self.queue.transaction() as conn:
                rows = await load_history(conn, user.user_id, run["config_snapshot"]["history"])
            snapshot = await prepare_history(run["config_snapshot"]["history"], rows, model, settings, content=definition.instruction, control=control)
            await self.queue.save_history(run, snapshot)
            history = RunHistoryProvider(self.service.storage.engine, user.user_id, user.tenant_id, snapshot, rows)
            agent = await build_research_agent(
                settings,
                workspace_dir=settings.workspace_root,
                model=model,
                checkpointer=self.checkpointer,
                unattended=TaskAgentOptions(
                    control,
                    search_authorized=bool(settings.tavily_api_key.strip()),
                    execution_at=run["started_at"],
                    scheduled_for=run["scheduled_for"],
                    history_provider=history,
                ),
            )
            return await execute_task_agent(
                agent,
                run_id=run["id"],
                instruction=definition.instruction,
                control=control,
                settings=settings,
                progress=progress,
                context=AgentContext(
                    user_id=user.user_id,
                    tenant_id=user.tenant_id,
                    tenant_name=user.tenant_name_zh,
                    run_id=str(run["id"]),
                    model_id=definition.model_id,
                ),
            )

        async def invoke_with_timeout():
            async with asyncio.timeout(settings.task_execution_timeout_seconds) as deadline:
                outcome = await invoke(deadline)
                # 执行核心会把取消转成终态；总时限触发的取消仍须归类为超时失败。
                if deadline.expired():
                    raise TimeoutError
                return outcome

        try:
            execution = asyncio.create_task(invoke_with_timeout())
            while not execution.done():
                await asyncio.wait({execution}, timeout=settings.task_poll_seconds)
                state = await self.queue.execution_status(run)
                owned = state["running"]
                if not owned or state["cancel_requested"]:
                    user_cancelled = state["cancel_requested"] and not execution.done()
                    control.stop()
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    if not owned:
                        logger.warning(
                            "task_id=%s run_id=%s ownership=lost", run["task_id"], run["id"]
                        )
                        return
                text = progress.text
                if not execution.done() and text != last_saved_text:
                    if not await self.queue.progress(run, text, **limits):
                        owned = False
                        return
                    last_saved_text = text
            outcome = await execution
        except asyncio.CancelledError:
            outcome = TaskOutcome(
                "cancelled" if user_cancelled else "failed",
                progress.text,
                True,
                "cancelled" if user_cancelled else "service_shutdown",
                "本地执行已停止，已发出的远端请求可能仍在处理。"
                if user_cancelled
                else "后台服务已停止，未自动重试。",
            )
        except TimeoutError:
            outcome = TaskOutcome("failed", progress.text, True, "execution_timeout", "任务准备或执行超过时限。")
        except (TaskError, TaskExecutionError) as exc:
            outcome = TaskOutcome(
                "failed",
                progress.text,
                True,
                getattr(exc, "error_code", None) or exc.code,
                str(exc),
            )
        except Exception:
            # 数据库异常也不得把连接地址、模型响应或凭据写进终态。
            outcome = TaskOutcome(
                "failed",
                progress.text,
                True,
                "executor_error",
                "后台执行失败，请检查服务状态。",
            )
        finally:
            control.stop()
            if execution is not None and not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
        if owned:
            try:
                committed = await self.queue.finish(
                    run,
                    status=outcome.status,
                    result=outcome.result_markdown,
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                    goal_complete=control.goal_complete,
                    **limits,
                )
                if committed:
                    logger.info(
                        "task_id=%s run_id=%s status=%s error_code=%s",
                        run["task_id"],
                        run["id"],
                        outcome.status,
                        outcome.error_code,
                    )
                else:
                    logger.warning(
                        "task_id=%s run_id=%s terminal_write=rejected", run["task_id"], run["id"]
                    )
            except Exception:
                logger.warning(
                    "task_id=%s run_id=%s 任务终态未提交，重启时将按中断处理。",
                    run["task_id"],
                    run["id"],
                )
