"""后台运行到普通会话的消息投影；无数据库、模型或凭据依赖。"""

from datetime import datetime
from uuid import UUID, uuid5

from melonclaw.core.task_schedule import BEIJING


def task_conversation(task):
    return {
        "id": uuid5(UUID(str(task["id"])), "conversation"),
        "user_id": task["owner_user_id"],
        "project_id": None,
        "title": f"任务：{task['name']}"[:200],
        "status": "active",
        "is_pinned": False,
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }


def task_request_id(run):
    return str(uuid5(UUID(str(run["id"])), "task-result"))


def assistant_values(run):
    status = {
        "queued": "pending",
        "running": "pending",
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }[run["status"]]
    metadata = {
        key: run.get(key)
        for key in ("status", "trigger_type", "error_code", "error_message", "result_is_partial")
    }
    metadata.update(
        run_id=str(run["id"]),
        task_id=str(run["task_id"]),
        name=run["config_snapshot"]["definition"]["name"],
        scheduled_for=run["scheduled_for"].isoformat() if run.get("scheduled_for") else None,
        cancel_requested=bool(run.get("cancel_requested_at")),
        queued_at=run["queued_at"].isoformat(),
    )
    duration = None
    if run.get("started_at") and run.get("finished_at"):
        duration = max(0, int((run["finished_at"] - run["started_at"]).total_seconds() * 1000))
    return {
        "content": run["result_markdown"],
        "model_display_name": run["config_snapshot"]["model_display_name"],
        "status": status,
        "error_code": run.get("error_code"),
        "display_metadata": {"task_run": metadata},
        "execution_duration_ms": duration,
        "updated_at": run.get("finished_at") or run.get("started_at") or run["queued_at"],
    }


def task_setup_message(task, *, seq=1):
    schedule = task["definition"]["schedule"]
    plan = {"manual": "手动执行", "once": "指定时间一次", "daily": "每天", "weekly": "每周"}[schedule["kind"]]
    if schedule["kind"] == "once":
        plan += "：" + datetime.fromisoformat(schedule["run_at"]).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") + "（北京时间）"
    elif schedule["kind"] in {"daily", "weekly"}:
        plan += f" {schedule['local_time']}（北京时间）"
        if schedule["kind"] == "weekly":
            plan += f"，星期{['一','二','三','四','五','六','日'][schedule['weekday']]}"
    return {
        "id": uuid5(UUID(str(task["id"])), f"task-settings:{task['version']}"), "conversation_id": task["conversation_id"],
        "request_id": str(uuid5(UUID(str(task["id"])), f"task-settings-request:{task['version']}")), "seq": seq,
        "role": "user", "content": f"{'创建任务' if task['version'] == 1 else '修改任务'}：{task['name']}\n执行计划：{plan}\n\n{task['definition']['instruction']}",
        "status": "completed", "display_metadata": {"task_setup": {"task_id": str(task["id"]) }},
        "created_at": task["created_at"], "updated_at": task["updated_at"],
    }


def task_message_rows(task, run, first_seq):
    definition = run["config_snapshot"]["definition"]
    return [{
        "conversation_id": task["conversation_id"], "request_id": task_request_id(run),
        "created_at": run["created_at"], "updated_at": run["created_at"],
        "assistant_steps": [], "execution_duration_ms": None, "error_code": None,
        "model_id": definition["model_id"], "model_provider": None, "model_name": None,
        "model_display_name": run["config_snapshot"]["model_display_name"],
        **assistant_values(run), "id": uuid5(UUID(str(run["id"])), "assistant"),
        "seq": first_seq, "role": "assistant",
    }]
