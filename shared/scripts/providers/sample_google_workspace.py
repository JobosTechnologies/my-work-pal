#!/usr/bin/env python3
"""Sample Google Workspace provider — seeded data, no network, no credentials.

WP5 stands the connection layer up before any real account is connected.
This provider implements the same :class:`WorkspaceClient` surface as the
live ``google_api`` provider (Gmail + Google Calendar through direct Google
APIs), but every read is served from seeded sample data shaped like the
Phase 1 pilot seed (see the Phase 1 Build Plan test script):

* today: a 10:00 viewing and a 15:00 appointment with a named customer;
* tomorrow: two afternoon events with distinct names and times;
* inbox: one urgent email needing a decision, one email from a named sender
  carrying a specific figure in its body, and one routine email.

Writes behave like the real provider's: drafts are recorded as drafts (never
sent), sends and calendar updates run through the same ``@guarded`` wrapper
(single-use grant + audit) as ``google_api``, and a send lands in an in-memory
outbox the caller can inspect. Nothing leaves the process.

State is per-instance and in-memory. Seeded reads are deterministic (they are
derived from the current date in the owner's timezone), so two instances
agree on reads; writes are visible only through the instance that made them.
That is deliberate: this provider exists so the morning brief, the Review
Queue and the WP4 approval path can run end to end in tests and demos before
Ibrahim connects a real account. It holds no credentials and is never a
fallback for the live provider — config chooses it explicitly.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from workspace_client import WorkspaceClient
from workspace_guardrails import guarded

OWNER_EMAIL = "owner@moejdiamonds.example"
NAMED_SENDER = "daniel.okafor@example.com"
NAMED_FIGURE = "USD 4,250"

import re

# Gmail-dialect tokenizer: parentheses, quoted phrases, and bare atoms.
_TOKEN_RE = re.compile(r'\(|\)|"[^"]*"|\S+')
_DAYS_RE = re.compile(r"(newer_than|older_than):(\d+)d")


def _owner_timezone(config: Any) -> ZoneInfo:
    """Resolve the owner's timezone from config (delivery → company → UTC)."""
    tz_name = ""
    if isinstance(config, Mapping):
        for section in ("delivery", "company"):
            block = config.get(section, {})
            if isinstance(block, Mapping) and block.get("timezone"):
                tz_name = str(block["timezone"])
                break
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:
        return ZoneInfo("UTC")


def _at(day: date, hh: int, mm: int, tz: ZoneInfo) -> str:
    return datetime.combine(day, time(hh, mm), tzinfo=tz).isoformat()


def _seed_events(tz: ZoneInfo) -> list[dict[str, Any]]:
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    return [
        {
            "id": "sample-evt-viewing",
            "summary": "Diamond viewing — Sarah Linden",
            "title": "Diamond viewing — Sarah Linden",
            "start": _at(today, 10, 0, tz),
            "end": _at(today, 10, 30, tz),
            "attendees": ["sarah.linden@example.com"],
            "organizer": OWNER_EMAIL,
            "location": "Showroom",
            "status": "confirmed",
        },
        {
            "id": "sample-evt-appointment",
            "summary": "Appointment — Marcus Bell, engagement ring fitting",
            "title": "Appointment — Marcus Bell, engagement ring fitting",
            "start": _at(today, 15, 0, tz),
            "end": _at(today, 16, 0, tz),
            "attendees": ["marcus.bell@example.com"],
            "organizer": OWNER_EMAIL,
            "location": "Showroom",
            "status": "confirmed",
        },
        {
            "id": "sample-evt-supplier",
            "summary": "Supplier call — Patel Gems, loose stones",
            "title": "Supplier call — Patel Gems, loose stones",
            "start": _at(tomorrow, 13, 30, tz),
            "end": _at(tomorrow, 14, 0, tz),
            "attendees": ["orders@patelgems.example"],
            "organizer": OWNER_EMAIL,
            "location": "Phone",
            "status": "confirmed",
        },
        {
            "id": "sample-evt-anniversary",
            "summary": "Appointment — Aisha Rahman, anniversary band",
            "title": "Appointment — Aisha Rahman, anniversary band",
            "start": _at(tomorrow, 16, 0, tz),
            "end": _at(tomorrow, 16, 30, tz),
            "attendees": ["aisha.rahman@example.com"],
            "organizer": OWNER_EMAIL,
            "location": "Showroom",
            "status": "confirmed",
        },
    ]


def _seed_messages(tz: ZoneInfo) -> list[dict[str, Any]]:
    today = datetime.now(tz).date()
    yesterday = today - timedelta(days=1)
    return [
        {
            "id": "sample-msg-urgent",
            "sender": "Leah Ortiz <leah.ortiz@brightline-secure.example>",
            "from": "Leah Ortiz <leah.ortiz@brightline-secure.example>",
            "to": OWNER_EMAIL,
            "subject": "URGENT: Insured shipment held — your decision needed today",
            "date": _at(today, 7, 12, tz),
            "thread_id": "thr-sample-shipment",
            "snippet": "The insured shipment is held at the courier hub. We need your release decision by 5 PM today.",
            "body": (
                "Good morning,\n\nThe insured shipment with the finished pieces is held at the "
                "courier hub because the declared value needs your confirmation. Please confirm "
                "today whether we release it as declared or split it into two shipments. We need "
                "your decision by 5 PM today to keep tomorrow's delivery slot.\n\n— Leah Ortiz, "
                "Brightline Secure Logistics"
            ),
            "tags": ["INBOX", "UNREAD", "IMPORTANT"],
            "unread": True,
            "has_attachments": False,
        },
        {
            "id": "sample-msg-daniel",
            "sender": f"Daniel Okafor <{NAMED_SENDER}>",
            "from": f"Daniel Okafor <{NAMED_SENDER}>",
            "to": OWNER_EMAIL,
            "subject": "Quote for the 2ct lab-grown parcel",
            "date": _at(today, 6, 40, tz),
            "thread_id": "thr-sample-parcel",
            "snippet": f"Your quote of {NAMED_FIGURE} works for us. Can you deliver by Thursday?",
            "body": (
                "Hello,\n\nThank you for the quote. The price of "
                f"{NAMED_FIGURE} for the 2ct lab-grown parcel works for us, and we would like to "
                "go ahead. One question before we confirm: can you deliver by Thursday? We need "
                "the stones for a fitting on Friday morning.\n\nBest regards,\nDaniel Okafor"
            ),
            "tags": ["INBOX", "UNREAD"],
            "unread": True,
            "has_attachments": False,
        },
        {
            "id": "sample-msg-routine",
            "sender": "Gem Weekly <digest@gemweekly.example>",
            "from": "Gem Weekly <digest@gemweekly.example>",
            "to": OWNER_EMAIL,
            "subject": "This week in the gem market",
            "date": _at(yesterday, 18, 5, tz),
            "thread_id": "thr-sample-gemweekly",
            "snippet": "Rapaport roundup, lab-grown price movements, and the week's auction results.",
            "body": (
                "This week in the gem market: lab-grown prices eased again, natural fancy "
                "yellows held firm, and two auction houses reported strong results for "
                "signed pieces. Full roundup inside."
            ),
            "tags": ["INBOX", "UNREAD"],
            "unread": True,
            "has_attachments": False,
        },
        {
            "id": "sample-msg-viewing",
            "sender": "Sarah Linden <sarah.linden@example.com>",
            "from": "Sarah Linden <sarah.linden@example.com>",
            "to": OWNER_EMAIL,
            "subject": "Re: Saturday viewing",
            "date": _at(yesterday, 15, 22, tz),
            "thread_id": "thr-sample-viewing",
            "snippet": "Saturday works for me. Looking forward to seeing the cushion cuts in person.",
            "body": (
                "Hi, Saturday works for me. Looking forward to seeing the cushion cuts in "
                "person — please keep the two we discussed aside if you can.\n\nThanks,\nSarah"
            ),
            "tags": ["INBOX"],
            "unread": False,
            "has_attachments": False,
        },
    ]


def _seed_files() -> list[dict[str, Any]]:
    return [
        {"id": "sample-file-pricelist", "name": "Price list — current.pdf", "mimeType": "application/pdf"},
        {"id": "sample-file-terms", "name": "Supplier terms — Patel Gems.docx",
         "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ]


def _seed_contacts() -> list[dict[str, Any]]:
    return [
        {"name": "Sarah Linden", "emails": ["sarah.linden@example.com"], "phones": ["+1-555-0101"]},
        {"name": "Marcus Bell", "emails": ["marcus.bell@example.com"], "phones": ["+1-555-0102"]},
        {"name": "Aisha Rahman", "emails": ["aisha.rahman@example.com"], "phones": ["+1-555-0103"]},
        {"name": "Daniel Okafor", "emails": [NAMED_SENDER], "phones": ["+1-555-0104"]},
    ]


def _parse_dt(value: str) -> datetime:
    text = str(value)
    if "T" not in text:
        text = f"{text}T00:00:00+00:00"
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class SampleGoogleWorkspaceClient(WorkspaceClient):
    """Seeded stand-in for the google_api provider (see module docstring)."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self._provider_name = "sample_google"
        self.tz = _owner_timezone(config)
        google_cfg = config.get("google", {}) if isinstance(config, Mapping) else {}
        self.owner_email = str(
            google_cfg.get("account_email") or google_cfg.get("delegate_email") or OWNER_EMAIL
        )
        self._events = _seed_events(self.tz)
        self._messages = _seed_messages(self.tz)
        self._files = _seed_files()
        self._contacts = _seed_contacts()
        self.sent: list[dict[str, Any]] = []
        self.drafts: list[dict[str, Any]] = []

    # ── Identity / health ──────────────────────────────────────────────

    def acting_identity(self, family: str) -> str | None:  # noqa: ARG002 - one sample mailbox
        return self.owner_email

    def health_check(self) -> bool:
        return True

    # ── Mail (reads) ───────────────────────────────────────────────────

    def _atom_matches(self, msg: Mapping[str, Any], atom: str) -> bool:
        """One Gmail-search atom against one message."""
        labels = {str(t).upper() for t in msg.get("tags", [])}
        low = atom.lower()
        if low == "is:unread":
            return bool(msg.get("unread"))
        if low == "is:read":
            return not msg.get("unread")
        if low.startswith("in:"):
            return low[3:].upper() in labels
        if low.startswith("label:"):
            return low[6:].upper() in labels
        if low.startswith("from:"):
            return low[5:] in str(msg.get("sender", "")).lower()
        if low.startswith("to:"):
            return low[3:] in str(msg.get("to", "")).lower()
        if low.startswith("subject:"):
            return low[8:] in str(msg.get("subject", "")).lower()
        if low == "has:attachment":
            return bool(msg.get("has_attachments"))
        if low.startswith(("newer_than:", "older_than:")):
            m = _DAYS_RE.match(low)
            if not m:
                return True
            try:
                when = _parse_dt(str(msg.get("date", "")))
            except ValueError:
                return True
            delta = datetime.now(when.tzinfo) - when
            return delta.days <= int(m.group(2)) if m.group(1) == "newer_than" else delta.days >= int(m.group(2))
        haystack = " ".join(
            str(msg.get(k, "")) for k in ("sender", "to", "subject", "body", "snippet")
        ).lower()
        return low.strip('"') in haystack

    def _matches(self, msg: Mapping[str, Any], query: str) -> bool:
        """Gmail-dialect match: AND of terms, OR groups, parentheses, quotes."""
        tokens = _TOKEN_RE.findall(str(query or ""))
        if not tokens:
            return True
        pos = 0

        def parse_expr() -> bool:
            nonlocal pos
            value = parse_and()
            while pos < len(tokens) and tokens[pos] == "OR":
                pos += 1
                rhs = parse_and()
                value = value or rhs
            return value

        def parse_and() -> bool:
            nonlocal pos
            value = True
            seen = False
            while pos < len(tokens) and tokens[pos] not in ("OR", ")"):
                value = parse_factor() and value
                seen = True
            return value if seen else True

        def parse_factor() -> bool:
            nonlocal pos
            tok = tokens[pos]
            if tok == "(":
                pos += 1
                value = parse_expr()
                if pos < len(tokens) and tokens[pos] == ")":
                    pos += 1
                return value
            pos += 1
            return self._atom_matches(msg, tok)

        return parse_expr()

    def mail_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        hits = [dict(m) for m in self._messages if self._matches(m, query)]
        return hits[:max_results]

    def mail_list_tags(self) -> list[dict[str, Any]]:
        tags = sorted({t for m in self._messages for t in m.get("tags", [])})
        return [{"id": t, "name": t} for t in tags]

    # ── Mail (writes — guarded, same contract as google_api) ───────────

    @guarded("gmail.draft", target_arg="to", audit_provider="sample_google")
    def mail_create_draft(self, to: str, subject: str, body: str,
                          cc: str | None = None) -> dict[str, Any]:
        draft = {
            "id": f"sample-msg-draft-{len(self.drafts) + 1:04d}",
            "draft_id": f"sample-draft-{len(self.drafts) + 1:04d}",
            "to": to, "subject": subject, "body": body, "cc": cc,
            "tags": ["DRAFT"],
        }
        self.drafts.append(draft)
        return {"id": draft["id"], "draft_id": draft["draft_id"]}

    @guarded("gmail.send", target_arg="to", audit_provider="sample_google",
             audit_tool="sample-mailbox")
    def mail_send(self, to: str, subject: str, body: str,
                  cc: str | None = None) -> dict[str, Any]:
        record = {"to": to, "subject": subject, "body": body, "cc": cc}
        self.sent.append(record)
        message = {
            "id": f"sample-msg-sent-{len(self.sent):04d}",
            "sender": self.owner_email,
            "from": self.owner_email,
            "to": to,
            "subject": subject,
            "body": body,
            "date": datetime.now(self.tz).isoformat(),
            "thread_id": "",
            "tags": ["SENT"],
            "unread": False,
        }
        self._messages.append(message)
        return {"message_id": message["id"], "simulated": True}

    # ── Calendar ───────────────────────────────────────────────────────

    def calendar_list(self, start: str, end: str) -> list[dict[str, Any]]:
        lo, hi = _parse_dt(start), _parse_dt(end)
        out = []
        for event in self._events:
            ev_start, ev_end = _parse_dt(event["start"]), _parse_dt(event["end"])
            if ev_start <= hi and ev_end >= lo and event.get("status") != "cancelled":
                out.append(dict(event))
        return sorted(out, key=lambda e: e["start"])

    @guarded("calendar.create", target_arg="title", audit_provider="sample_google")
    def calendar_create(self, title: str, start: str, end: str,
                        attendees: list[str] | None = None,
                        description: str | None = None) -> dict[str, Any]:
        event = {
            "id": f"sample-evt-new-{len(self._events) + 1:04d}",
            "summary": title, "title": title,
            "start": start, "end": end,
            "attendees": attendees or [], "organizer": self.owner_email,
            "location": "", "status": "confirmed",
            "description": description or "",
        }
        self._events.append(event)
        return dict(event)

    @guarded("calendar.update", target_arg="event_id", audit_provider="sample_google")
    def calendar_update(self, event_id: str, **fields: Any) -> dict[str, Any]:
        for event in self._events:
            if event["id"] == event_id:
                for key, value in fields.items():
                    event["summary" if key == "title" else key] = value
                    if key == "title":
                        event["title"] = value
                return dict(event)
        raise RuntimeError(f"sample calendar: no event with id {event_id!r}")

    @guarded("calendar.cancel", target_arg="event_id", audit_provider="sample_google")
    def calendar_cancel(self, event_id: str) -> dict[str, Any]:
        return self.calendar_update(event_id, status="cancelled")

    # ── Files / contacts ───────────────────────────────────────────────

    def files_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        needle = str(query or "").replace("name contains", "").strip().strip("'\"").lower()
        hits = [dict(f) for f in self._files if not needle or needle in f["name"].lower()]
        return hits[:max_results]

    def files_upload(self, file_path: str, parent_id: str | None = None) -> dict[str, Any]:
        record = {"id": f"sample-file-up-{len(self._files) + 1:04d}",
                  "name": Path(str(file_path)).name, "parent_id": parent_id}
        self._files.append(record)
        return dict(record)

    def contacts_list(self, max_results: int = 50) -> list[dict[str, Any]]:
        return [dict(c) for c in self._contacts[:max_results]]
