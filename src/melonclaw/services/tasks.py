"""表单与聊天共用的任务保存、当前配置运行与北京时间调度。"""

import logging
from uuid import UUID

from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_schedule import due_decision
from melonclaw.core.task_snapshot import TaskDefinition
from melonclaw.repository.task_scheduling import TaskScheduleRepository
from melonclaw.repository.tasks import json_value
from melonclaw.services.model_resolution import ModelResolutionMixin

logger = logging.getLogger(__name__)


def public_run(row):
    return json_value(row)


class TaskService(ModelResolutionMixin):
    def __init__(self, settings, storage):
        self.settings, self.storage = settings, storage
        self.repository = TaskScheduleRepository(storage.engine, operation_retention_days=settings.task_operation_retention_days)

    def require_ready(self):
        return self.storage

    async def default_model_id(self, user_id, *, connection=None):
        try:
            model = await self.resolve_system_default_model(user_id, connection=connection)
            return model.profile_id
        except ValueError as exc:
            raise TaskError("system_default_model_unavailable", str(exc), 422) from exc

    async def validate_definition(self, user_id, definition, *, connection=None):
        try:
            return await self.resolve_model(user_id, definition.model_id, connection=connection)
        except ValueError as exc:
            raise TaskError(
                "model_unavailable", "所选模型不存在、已停用或凭据不可用，请先配置模型。", 422
            ) from exc

    @staticmethod
    def snapshot(task, definition, model):
        # 只保留可读审计信息；连接、凭据、工具权限不进入快照。
        return {
            "definition": definition.model_dump(mode="json"),
            "task_version": task["version"],
            "model_display_name": model.display_name,
        }

    @staticmethod
    def editable(task, version):
        if task["status"] == "archived":
            raise TaskError("task_archived", "已归档任务只读，不能修改或运行。")
        if task["version"] != version:
            raise TaskError("version_conflict", "任务配置已变化，请刷新后重新确认。")

    async def save_definition(self, tx, definition, *, enabled, current=None,
                              conversation_id=None, record_setup=True, run_now=False):
        """在调用方已校验身份/版本的事务内，统一模型、计划和保存规则。"""
        model = await self.validate_definition(tx.user.user_id, definition, connection=tx.conn)
        try:
            task = await tx.save(definition, enabled=enabled, current=current,
                                 conversation_id=conversation_id, record_setup=record_setup)
        except ValueError as exc:
            raise TaskError("invalid_schedule", str(exc), 422) from exc
        run = await tx.enqueue(task, self.snapshot(task, definition, model)) if run_now else None
        return {"task": json_value(task), "run": public_run(run) if run else None}

    async def save(
        self,
        user_id,
        *,
        definition,
        enabled,
        idempotency_key,
        task_id=None,
        expected_version=None,
        run_now=False,
    ):
        definition = TaskDefinition.model_validate(definition)
        request = dict(
            action="save",
            task_id=task_id,
            expected_version=expected_version,
            definition=definition.model_dump(mode="json"),
            enabled=enabled,
            run_now=run_now,
        )
        async with self.repository.operation(user_id, idempotency_key, request) as tx:
            if tx.replay is not None:
                return tx.replay
            current = await tx.task(task_id) if task_id else None
            if current:
                self.editable(current, expected_version)
            elif expected_version is not None:
                raise TaskError("version_conflict", "新任务不应携带旧版本。")
            tx.result = await self.save_definition(tx, definition, enabled=enabled,
                                                   current=current, run_now=run_now)
            return tx.result

    async def run(self, user_id, task_id, *, idempotency_key, expected_version):
        async with self.repository.operation(
            user_id,
            idempotency_key,
            {
                "action": "run",
                "task_id": task_id,
                "expected_version": expected_version,
            },
        ) as tx:
            if tx.replay is not None:
                return tx.replay
            task = await tx.task(task_id)
            self.editable(task, expected_version)
            definition = TaskDefinition.model_validate(task["definition"])
            model = await self.validate_definition(user_id, definition, connection=tx.conn)
            tx.result = public_run(await tx.enqueue(task, self.snapshot(task, definition, model)))
            return tx.result

    async def archive(self, user_id, task_id, *, idempotency_key, expected_version):
        async with self.repository.operation(
            user_id,
            idempotency_key,
            {
                "action": "archive",
                "task_id": task_id,
                "expected_version": expected_version,
            },
        ) as tx:
            if tx.replay is not None:
                return tx.replay
            task = await tx.task(task_id)
            self.editable(task, expected_version)
            tx.result = json_value(await tx.archive(task))
            return tx.result

    async def set_enabled(self, user_id, task_id, *, enabled, idempotency_key, expected_version):
        async with self.repository.operation(
            user_id,
            idempotency_key,
            {
                "action": "enable",
                "task_id": task_id,
                "enabled": enabled,
                "expected_version": expected_version,
            },
        ) as tx:
            if tx.replay is not None:
                return tx.replay
            task = await tx.task(task_id)
            self.editable(task, expected_version)
            definition = TaskDefinition.model_validate(task["definition"])
            if enabled:
                await self.validate_definition(user_id, definition, connection=tx.conn)
            tx.result = json_value(await tx.save(definition, enabled=enabled, current=task))
            return tx.result

    async def cancel(self, user_id, run_id, *, idempotency_key):
        async with self.repository.operation(
            user_id, idempotency_key, {"action": "cancel", "run_id": run_id}
        ) as tx:
            if tx.replay is not None:
                return tx.replay
            tx.result = public_run(await tx.cancel(await tx.run(run_id)))
            return tx.result

    async def execution_input(self, run):
        context = await self.storage.get_user_context(run["owner_user_id"])
        if context is None or context.tenant_id != run["tenant_id"]:
            raise TaskError("identity_changed", "任务所属用户或租户已失效。", 403)
        definition = TaskDefinition.model_validate(run["config_snapshot"]["definition"])
        model = await self.validate_definition(context.user_id, definition)
        return context, definition, model

    async def get_run(self, user_id, run_id: UUID):
        return public_run(await self.repository.get_run(user_id, run_id))

    async def dispatch_due(self, *, at=None, limit=100):
        dispatched = 0
        for _ in range(limit):
            async with self.repository.due(at=at) as item:
                if item is None:
                    break
                tx, task, timestamp, active = item
                definition = TaskDefinition.model_validate(task["definition"])
                decision = due_decision(
                    definition.schedule,
                    next_run_at=task["next_run_at"],
                    now=timestamp,
                    active_run=active,
                )
                if decision is None:
                    break
                if decision.skip_reason:
                    logger.info("task_id=%s schedule_skipped=%s", task["id"], decision.skip_reason)
                else:
                    # 模型可用性在领取后统一检查；入队和推进计划仍在同一事务。
                    await tx.enqueue(
                        task,
                        {
                            "definition": definition.model_dump(mode="json"),
                            "task_version": task["version"],
                            "model_display_name": definition.model_id,
                        },
                        scheduled_for=decision.scheduled_for,
                    )
                await tx.advance_schedule(task["id"], decision.next_run_at)
                dispatched += 1
        return dispatched

    async def cleanup_checkpoints(self, checkpointer, *, at=None):
        await self.repository.retire_operations(at=at)
        candidates = await self.repository.cleanup_candidates(
            self.settings.task_checkpoint_retention_days, at=at
        )
        for run in candidates:
            await checkpointer.adelete_thread(run["execution_context_id"])
            await self.repository.mark_cleaned(run["id"])
        return len(candidates)
