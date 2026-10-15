"""北京时间的分钟精度计划；错过不补跑，忙时跳过。"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
# 容纳正常轮询延迟，不作为停机补跑窗口。
SCHEDULE_GRACE_SECONDS = 60


class TaskSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["manual", "once", "daily", "weekly"]
    run_at: datetime | None = None
    local_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(default=None, ge=0, le=6, strict=True)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.kind == "manual":
            if any(value is not None for value in (self.run_at, self.local_time, self.weekday)):
                raise ValueError("手动任务不能包含时间配置。")
        elif self.kind == "once":
            if self.run_at is None or self.local_time is not None or self.weekday is not None:
                raise ValueError("一次性任务必须且只能指定 run_at。")
            value = utc(self.run_at)
            if value.second or value.microsecond:
                raise ValueError("计划时间只支持分钟精度。")
        else:
            if self.run_at is not None or self.local_time is None:
                raise ValueError("循环任务必须指定时分。")
            if (self.kind == "weekly") != (self.weekday is not None):
                raise ValueError("每周任务须指定星期（0=周一）；每天任务不指定星期。")
        return self

    def validate_future(self, now):
        if self.kind == "once" and utc(self.run_at) <= utc(now):
            raise ValueError("一次性任务必须选择未来时间。")


def utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区。")
    return value.astimezone(UTC)


def next_run(schedule: TaskSchedule, after: datetime) -> datetime | None:
    after = utc(after)
    if schedule.kind == "manual":
        return None
    if schedule.kind == "once":
        value = utc(schedule.run_at)
        return value if value > after else None
    local = after.astimezone(BEIJING)
    candidate = datetime.combine(local.date(), time.fromisoformat(schedule.local_time), BEIJING)
    if schedule.kind == "weekly":
        candidate += timedelta(days=(schedule.weekday - candidate.weekday()) % 7)
    if candidate <= local:
        candidate += timedelta(days=7 if schedule.kind == "weekly" else 1)
    return candidate.astimezone(UTC)


@dataclass(frozen=True)
class DispatchDecision:
    scheduled_for: datetime
    next_run_at: datetime | None
    skip_reason: str | None = None


def due_decision(schedule, *, next_run_at, now, active_run=False):
    if schedule.kind == "manual" or next_run_at is None or utc(next_run_at) > utc(now):
        return None
    scheduled_for = utc(next_run_at)
    reason = None
    if (utc(now) - scheduled_for).total_seconds() > SCHEDULE_GRACE_SECONDS:
        reason = "missed_schedule"
    elif active_run:
        reason = "overlap"
    return DispatchDecision(scheduled_for, next_run(schedule, now), reason)
