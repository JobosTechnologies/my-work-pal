#!/usr/bin/env python3
"""Outbound calls: the morning brief and urgent-attention calls (WP2).

The rules live here, in code, not in prompts (the plan is explicit about
that):

* The morning call is registered as a job in the foundation's standalone
  cron store, at the time the owner set. The planner below decides what a
  firing means when it happens.
* No outbound calls inside quiet hours, except a wake-up call the owner set
  for that time. A call that would land in quiet hours is held and logged,
  not placed.
* A missed scheduled call retries once after 10 minutes, then once more
  after 30 minutes, then stops and leaves a note.
* Urgent calls stop at the daily limit the owner set.
* Every outbound call, answered or not, is logged with its reason and its
  outcome. A "bad time" answer is noted, the call ends quickly, and one
  later time can be recorded with it.

Nothing here dials anything. The planner returns decisions; placing the
call is the telephony step that arrives with number provisioning (a later
WP2 stage). All logic takes an explicit ``now`` so it can be proven in
tests with a fake clock, and the call log is a plain JSONL file beside the
cron store.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import cron_store

MORNING_JOB_ID = "mwp-morning-brief-call"
RETRY_DELAYS = (timedelta(minutes=10), timedelta(minutes=30))
MAX_ATTEMPTS = 1 + len(RETRY_DELAYS)  # first try + two retries, then stop


@dataclass
class CallSettings:
    """The owner's call settings: set once by the owner, never guessed."""

    owner_name: str
    business_name: str
    assistant_name: str = "Pal"
    timezone: str = "America/New_York"
    morning_call_time: time = time(8, 0)
    quiet_start: time = time(21, 0)
    quiet_end: time = time(7, 0)
    morning_is_wakeup: bool = False  # owner designated the morning call a wake-up call
    urgent_daily_limit: int = 3

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass
class Decision:
    action: str  # "place" | "hold" | "stop"
    reason: str
    next_attempt_at: datetime | None = None
    note: str = ""  # a thread note, when the plan calls for one


class CallLog:
    """Append-only JSONL record of every outbound decision and outcome."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, kind: str, action: str, reason: str, at: datetime, **extra: Any) -> dict[str, Any]:
        entry = {
            "ts": at.isoformat(),
            "kind": kind,
            "action": action,
            "reason": reason,
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def count_on(self, kind: str, day: datetime, action: str | None = None) -> int:
        day_str = day.date().isoformat()
        return sum(
            1
            for e in self.entries()
            if e.get("kind") == kind
            and str(e.get("ts", "")).startswith(day_str)
            and (action is None or e.get("action") == action)
        )


def in_quiet_hours(now: datetime, settings: CallSettings) -> bool:
    """Quiet hours may wrap midnight (the usual 21:00-07:00 shape)."""
    local = now.astimezone(settings.tz).time()
    start, end = settings.quiet_start, settings.quiet_end
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def register_morning_call(settings: CallSettings, cron_path: Path | None = None) -> Path:
    """Write (or refresh) the morning-brief job in the foundation cron store."""
    t = settings.morning_call_time
    job = {
        "id": MORNING_JOB_ID,
        "name": "My Work Pal morning brief call",
        "schedule": f"{t.minute} {t.hour} * * *",
        "prompt": (
            f"Place the morning brief call to {settings.owner_name} "
            f"({settings.business_name}): read the daily brief, then walk "
            "the Review Queue. Honour quiet hours and the retry rule."
        ),
        "skills": [],
        "deliver": "voice-call",
        "kind": "morning_brief_call",
        "timezone": settings.timezone,
    }
    return cron_store.upsert_job(job, cron_path)


class OutboundPlanner:
    """Decides place / hold / stop for outbound calls. Never dials."""

    def __init__(self, settings: CallSettings, log: CallLog) -> None:
        self.settings = settings
        self.log = log

    # -- scheduled morning brief ------------------------------------------

    def plan_morning_call(self, now: datetime, *, attempt: int = 0) -> Decision:
        """What to do when the morning job fires (or a retry comes due)."""
        if in_quiet_hours(now, self.settings):
            if attempt == 0 and self.settings.morning_is_wakeup:
                decision = Decision("place", "owner-set wake-up call")
            else:
                decision = Decision(
                    "hold",
                    "quiet hours: held and logged, not placed",
                    note="Morning brief call held: it landed in quiet hours.",
                )
                self.log.append("morning_brief", "hold", decision.reason, now, attempt=attempt)
                return decision
        else:
            decision = Decision("place", "scheduled morning brief")
        self.log.append("morning_brief", decision.action, decision.reason, now, attempt=attempt)
        return decision

    def record_morning_outcome(
        self, now: datetime, *, answered: bool, attempt: int
    ) -> Decision:
        """Apply the retry rule after a morning call attempt.

        Attempt 0 unanswered -> retry in 10 minutes. Attempt 1 unanswered
        -> retry in 30 minutes. Attempt 2 unanswered -> stop, with a note.
        """
        if answered:
            self.log.append("morning_brief", "answered", "owner answered", now, attempt=attempt)
            return Decision("stop", "answered")
        self.log.append("morning_brief", "no_answer", "no answer", now, attempt=attempt)
        if attempt < len(RETRY_DELAYS):
            nxt = now + RETRY_DELAYS[attempt]
            # A retry that would land in quiet hours is held instead.
            if in_quiet_hours(nxt, self.settings):
                self.log.append("morning_brief", "hold", "retry landed in quiet hours", nxt, attempt=attempt + 1)
                return Decision(
                    "stop",
                    "retry landed in quiet hours; held",
                    note="Morning call missed; the retry landed in quiet hours and was held.",
                )
            return Decision("place", f"retry {attempt + 1} after no answer", next_attempt_at=nxt)
        return Decision(
            "stop",
            "no answer after the final retry",
            note="Morning call missed after two retries. No further calls today.",
        )

    # -- urgent-attention calls ---------------------------------------------

    def request_urgent_call(self, now: datetime, reason: str) -> Decision:
        """An urgent-attention trigger: limited per day, quiet in quiet hours."""
        placed_today = self.log.count_on("urgent", now, action="place")
        if placed_today >= self.settings.urgent_daily_limit:
            decision = Decision("hold", f"daily urgent-call limit ({self.settings.urgent_daily_limit}) reached")
        elif in_quiet_hours(now, self.settings):
            decision = Decision("hold", "quiet hours: urgent call held and logged")
        else:
            decision = Decision("place", f"urgent: {reason}")
        self.log.append("urgent", decision.action, reason, now)
        return decision

    # -- the owner says it is a bad time -------------------------------------

    def record_bad_time(self, now: datetime, *, later: str | None = None) -> Decision:
        """Note it, end quickly, and record the one later time offered."""
        note = "Owner said it was a bad time."
        if later:
            note += f" Call again: {later}."
        self.log.append("call", "bad_time", note, now)
        return Decision("stop", "bad time", note=note)
