"""固定北京时间的分钟计划：未来执行、错过不补跑、忙时跳过。"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from melonclaw.core.task_schedule import TaskSchedule, due_decision, next_run


def dt(value):
    return datetime.fromisoformat(value).astimezone(UTC)


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "daily", "local_time": "25:00"},
        {"kind": "weekly", "local_time": "06:00", "weekday": 7},
        {"kind": "weekly", "local_time": "06:00", "weekday": True},
        {"kind": "weekly", "local_time": "06:00"},
        {"kind": "daily", "local_time": "06:00", "weekday": 1},
        {"kind": "manual", "local_time": "06:00"},
        {"kind": "manual", "timezone": "Asia/Shanghai"},
        {"kind": "once", "run_at": "2026-10-10T06:00:00"},
        {"kind": "once", "run_at": "2026-10-10T06:00:01+08:00"},
        {"kind": "cron", "cron": "* * * * *"},
    ],
)
def test_reject_invalid_schedule(fields):
    with pytest.raises(ValidationError):
        TaskSchedule(**fields)


def test_three_cycles_and_exact_minute_are_once_each():
    schedule = TaskSchedule(kind="daily", local_time="06:00")
    slot = next_run(schedule, dt("2026-10-09T05:00+08:00"))
    times = []
    for _ in range(3):
        decision = due_decision(schedule, next_run_at=slot, now=slot)
        assert decision.skip_reason is None
        times.append(decision.scheduled_for)
        assert due_decision(schedule, next_run_at=decision.next_run_at, now=slot) is None
        slot = decision.next_run_at
    assert times == [dt(f"2026-10-{day}T06:00+08:00") for day in ("09", "10", "11")]


def test_weekly_calendar_and_manual():
    schedule = TaskSchedule(kind="weekly", local_time="06:00", weekday=5)
    slot = next_run(schedule, dt("2026-10-09T00:00Z"))
    assert slot == dt("2026-10-10T06:00+08:00")
    assert next_run(TaskSchedule(kind="manual"), datetime.now(UTC)) is None
    assert next_run(schedule, slot) - slot == timedelta(days=7)


@pytest.mark.parametrize("delay,reason", [(60, None), (61, "missed_schedule")])
def test_only_normal_poll_delay_is_tolerated(delay, reason):
    schedule = TaskSchedule(kind="daily", local_time="06:00")
    start = dt("2026-10-09T06:00+08:00")
    decision = due_decision(schedule, next_run_at=start, now=start + timedelta(seconds=delay))
    assert decision.skip_reason == reason
    assert decision.next_run_at == dt("2026-10-10T06:00+08:00")


def test_downtime_skips_all_missed_cycles_without_replay():
    schedule = TaskSchedule(kind="daily", local_time="06:00")
    decision = due_decision(
        schedule, next_run_at=dt("2020-10-09T06:00+08:00"), now=dt("2026-10-09T06:00+08:00")
    )
    assert decision.skip_reason == "missed_schedule"
    assert decision.next_run_at == dt("2026-10-10T06:00+08:00")


def test_once_expired_and_overlap():
    schedule = TaskSchedule(kind="once", run_at="2026-10-09T06:00+08:00")
    with pytest.raises(ValueError):
        schedule.validate_future(schedule.run_at)
    schedule.validate_future(dt("2026-10-09T05:00+08:00"))
    assert next_run(schedule, schedule.run_at) is None
    decision = due_decision(
        schedule, next_run_at=schedule.run_at, now=schedule.run_at, active_run=True
    )
    assert decision.skip_reason == "overlap" and decision.next_run_at is None
    assert due_decision(schedule, next_run_at=None, now=schedule.run_at) is None
    with pytest.raises(ValueError):
        next_run(schedule, datetime(2026, 10, 9))
