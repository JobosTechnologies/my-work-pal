"""WP2 outbound rules: quiet hours, the 10/30 retry pattern, urgent limit.

All decisions are taken against a fake clock; the call log is the evidence.
"""
from __future__ import annotations

import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared" / "scripts", ROOT / "voice"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cron_store  # noqa: E402
from mwp_voice.scheduler import (  # noqa: E402
    MORNING_JOB_ID,
    CallLog,
    CallSettings,
    OutboundPlanner,
    in_quiet_hours,
    register_morning_call,
)

ET = ZoneInfo("America/New_York")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 12, 1, hour, minute, tzinfo=ET)


def _settings(**kw) -> CallSettings:
    base = dict(owner_name="Demo Owner", business_name="Sample Co")
    base.update(kw)
    return CallSettings(**base)


def _planner(tmp_path, **kw) -> OutboundPlanner:
    return OutboundPlanner(_settings(**kw), CallLog(tmp_path / "calls.jsonl"))


def test_morning_call_registers_in_the_cron_store(tmp_path):
    path = tmp_path / "cron" / "jobs.json"
    register_morning_call(_settings(morning_call_time=time(8, 0)), path)
    jobs = cron_store.load_jobs(path)
    mine = [j for j in jobs if j.get("id") == MORNING_JOB_ID]
    assert len(mine) == 1
    assert mine[0]["schedule"] == "0 8 * * *"
    assert mine[0]["deliver"] == "voice-call"


def test_quiet_hours_wrap_midnight():
    settings = _settings()  # 21:00 - 07:00
    assert in_quiet_hours(_at(22, 0), settings)
    assert in_quiet_hours(_at(6, 59), settings)
    assert not in_quiet_hours(_at(7, 0), settings)
    assert not in_quiet_hours(_at(8, 0), settings)
    assert not in_quiet_hours(_at(20, 59), settings)


def test_morning_call_placed_on_time_and_held_in_quiet_hours(tmp_path):
    planner = _planner(tmp_path)
    assert planner.plan_morning_call(_at(8, 0)).action == "place"
    held = planner.plan_morning_call(_at(6, 30))
    assert held.action == "hold"
    assert held.note  # a thread note is produced
    kinds = [(e["kind"], e["action"]) for e in planner.log.entries()]
    assert ("morning_brief", "place") in kinds
    assert ("morning_brief", "hold") in kinds


def test_owner_set_wakeup_call_is_the_quiet_hours_exception(tmp_path):
    planner = _planner(tmp_path, morning_is_wakeup=True, morning_call_time=time(6, 30))
    assert planner.plan_morning_call(_at(6, 30)).action == "place"


def test_retry_pattern_is_10_then_30_then_stop(tmp_path):
    planner = _planner(tmp_path)
    first = planner.record_morning_outcome(_at(8, 0), answered=False, attempt=0)
    assert first.action == "place" and first.next_attempt_at == _at(8, 10)
    second = planner.record_morning_outcome(_at(8, 10), answered=False, attempt=1)
    assert second.action == "place" and second.next_attempt_at == _at(8, 40)
    third = planner.record_morning_outcome(_at(8, 40), answered=False, attempt=2)
    assert third.action == "stop" and third.note
    answered = planner.record_morning_outcome(_at(8, 0), answered=True, attempt=0)
    assert answered.action == "stop" and answered.reason == "answered"


def test_retry_landing_in_quiet_hours_is_held_not_placed(tmp_path):
    planner = _planner(tmp_path, morning_call_time=time(20, 50))
    outcome = planner.record_morning_outcome(_at(20, 50), answered=False, attempt=0)
    assert outcome.action == "stop"
    assert "quiet hours" in outcome.reason


def test_urgent_calls_stop_at_the_daily_limit(tmp_path):
    planner = _planner(tmp_path, urgent_daily_limit=2)
    assert planner.request_urgent_call(_at(10, 0), "a client is waiting").action == "place"
    assert planner.request_urgent_call(_at(11, 0), "second").action == "place"
    third = planner.request_urgent_call(_at(12, 0), "third")
    assert third.action == "hold" and "limit" in third.reason


def test_urgent_call_in_quiet_hours_is_held_and_logged(tmp_path):
    planner = _planner(tmp_path)
    decision = planner.request_urgent_call(_at(23, 0), "late night trigger")
    assert decision.action == "hold"
    assert planner.log.entries()[-1]["action"] == "hold"


def test_bad_time_is_noted_with_the_later_time(tmp_path):
    planner = _planner(tmp_path)
    decision = planner.record_bad_time(_at(9, 0), later="4 pm")
    assert decision.action == "stop"
    assert "4 pm" in decision.note
    assert planner.log.entries()[-1]["action"] == "bad_time"
