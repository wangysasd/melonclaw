"""聊天任务配置编排；复用任务事务，不接收模型提供的身份或任务编号。"""

from uuid import UUID, uuid5

from pydantic import ValidationError

from melonclaw.core.task_configuration import TaskChanges
from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_schedule import BEIJING
from melonclaw.core.task_snapshot import TaskDefinition
from melonclaw.repository.task_configuration import conversation_task
from melonclaw.repository.tasks import TaskTransaction, json_value, task_lock
from melonclaw.services.tasks import TaskService


class TaskConfigurationService:
    def __init__(self, runtime):
        self.runtime = runtime

    async def configure(self, context, call_id, *, action="inspect", changes=None,
                        expected_version=None):
        try:
            if context is None or not context.conversation_id or not context.user_message_id or not context.request_id:
                raise TaskError("invalid_context", "任务配置需要有效的普通聊天上下文。", 403)
            if context.project_id:
                raise TaskError("project_task_forbidden", "请在普通会话中配置任务，任务不能绑定项目。", 422)
            conversation_id, message_id = UUID(context.conversation_id), UUID(context.user_message_id)
            storage = self.runtime.require_ready()
            service = TaskService(self.runtime.settings, storage)
            if action == "inspect":
                async with storage.engine.begin() as conn:
                    await task_lock(conn)
                    user = await storage.get_user_context(context.user_id, connection=conn)
                    self.identity(context, user)
                    current = await conversation_task(conn, user, conversation_id, message_id, context.request_id)
                    timestamp = await TaskTransaction(conn, user).current_time()
                models = await self.runtime.models(context.user_id)
                return {"status": "ready", "version": current["version"] if current else 0,
                        "task": json_value(current) if current else None,
                        "default_model_id": next((m["id"] for m in models["items"]
                                                  if m["available"] and m["scope"] == "global" and m["is_default"]), None),
                        "available_models": [{"id": m["id"], "name": m["display_name"]}
                                             for m in models["items"] if m["available"]],
                        "now": timestamp.astimezone(BEIJING).isoformat(), "timezone": "Asia/Shanghai",
                        "schedule_kinds": ["manual", "once", "daily", "weekly"]}
            if action != "save" or not call_id or type(expected_version) is not int or expected_version < 0:
                raise TaskError("invalid_configuration", "保存需要配置内容和读取到的版本。", 422)
            changes = TaskChanges.model_validate(changes)
            patch = changes.patch()
            if not patch:
                raise TaskError("empty_configuration", "请提供需要修改的配置。", 422)
            key = str(uuid5(message_id, f"task-configuration:{call_id}"))
            request = {"action": "chat_configure", "conversation_id": str(conversation_id),
                       "message_id": str(message_id), "request_id": context.request_id,
                       "expected_version": expected_version, "changes": patch}
            async with service.repository.operation(context.user_id, key, request) as tx:
                self.identity(context, tx.user)
                current = await conversation_task(tx.conn, tx.user, conversation_id, message_id,
                                                  context.request_id,
                                                  require_pending=tx.replay is None)
                if tx.replay is not None:
                    return tx.replay
                if current:
                    service.editable(current, expected_version)
                    definition = dict(current["definition"])
                    enabled = current["status"] == "enabled"
                else:
                    if expected_version != 0:
                        raise TaskError("version_conflict", "本会话尚无任务，请重新读取配置。")
                    definition, enabled = {}, True
                enabled = patch.pop("enabled", enabled)
                definition.update(patch)
                if current is None and "model_id" not in definition:
                    definition["model_id"] = await service.default_model_id(context.user_id, connection=tx.conn)
                definition = TaskDefinition.model_validate(definition)
                # 单纯暂停不依赖旧模型的凭据可用性，和表单启停入口一致。
                if current and not patch and not enabled:
                    task = await tx.save(definition, enabled=False, current=current, record_setup=False)
                    saved = {"task": json_value(task), "run": None}
                else:
                    saved = await service.save_definition(tx, definition, enabled=enabled, current=current,
                                                          conversation_id=conversation_id,
                                                          record_setup=False)
                task = saved["task"]
                next_at = task["next_run_at"] if task["status"] == "enabled" else None
                tx.result = {"status": "saved", "task": task,
                             "next_run_at_beijing": self.local_time(next_at) if next_at else None,
                             "message": "配置已保存，后续运行使用新配置；本次未立即执行，已有运行保持原快照。"}
                return tx.result
        except TaskError as exc:
            return {"status": "error", "code": exc.error_code, "message": str(exc)}
        except (ValidationError, ValueError, TypeError):
            return {"status": "error", "code": "invalid_configuration",
                    "message": "配置参数不完整或格式无效，请核对任务名称、完整要求、模型和执行时间。"}
        except Exception:  # noqa: BLE001 - 不把数据库异常、连接或凭据返回给模型
            return {"status": "error", "code": "configuration_unconfirmed",
                    "message": "未确认配置是否保存成功，请读取实际配置核实；重试相同工具调用不会重复提交。"}

    @staticmethod
    def identity(context, user):
        if user is None or user.tenant_id != context.tenant_id:
            raise TaskError("identity_changed", "当前用户或租户归属已失效。", 403)

    @staticmethod
    def local_time(value):
        from datetime import datetime

        return datetime.fromisoformat(value).astimezone(BEIJING).isoformat()
