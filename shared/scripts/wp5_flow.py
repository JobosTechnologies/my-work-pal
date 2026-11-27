#!/usr/bin/env python3
"""WP5 verification flow: connections, brief, and guarded writes on sample data.

Runs the whole WP5 path against the ``sample_google`` provider (seeded
Gmail + Calendar, no credentials, no network) in a throwaway workspace:

    1. Connection health   the structured report says connected (sample mode)
    2. Morning brief       schedule, unread email, follow-ups and pending
                           approvals assembled through the daily loop's own
                           collectors, reading through the provider
    3. One named email     fetched in full; the figure in its body checks out
    4. Draft reply         queued in the Review Queue with its preview —
                           and provably unsent
    5. Approved send       signed approval, then the guarded execution path;
                           a second execution attempt changes nothing
    6. Approved move       the 3 PM appointment moves once, to tomorrow 4:30 PM
    7. Audit               both actions are on the record, end to end

Nothing here touches a real account: the provider is the seeded sample, the
mailbox is in-memory, and the workspace is deleted at the end (``--keep DIR``
keeps it for inspection). This is the WP5 gate from the Phase 1 Build Plan,
run against mocked responses exactly as the plan's WP5 stage prescribes —
live data waits for the pilot, where a real account is connected.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
_SKILL_SCRIPTS = _SCRIPT_DIR.parents[1] / "skills" / "daily-briefing" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

PLUGIN_ROOT = _SCRIPT_DIR.parents[1]
APPROVER = "wp5-owner"
TIMEZONE = "America/New_York"
MARKER = ".cos-demo"  # reuse the demo's keep/reset safety convention


def _prepare(root: Path) -> Path:
    import yaml
    from chief_of_staff import _DEMO_CONFIG

    examples = PLUGIN_ROOT / "examples"
    cfg = dict(_DEMO_CONFIG)
    cfg["company"] = {**_DEMO_CONFIG["company"], "name": "Moe J Diamonds (SAMPLE)"}
    cfg["delivery"] = {**_DEMO_CONFIG["delivery"], "timezone": TIMEZONE, "briefing_time": "08:00"}
    cfg["integrations"] = {"workspace": {"provider": "sample_google", "mode": "direct"}}
    cfg["google"] = {
        "account_email": "owner@moejdiamonds.example",
        "delegate_email": "owner@moejdiamonds.example",
    }
    cfg["paths"] = {"project_root": str(root), "wiki_path": str(root / "wiki"),
                    "templates": str(PLUGIN_ROOT / "shared" / "templates")}
    for name in ("pipeline.yaml", "invoices.yaml", "expenses.yaml", "todos.yaml"):
        if (examples / name).exists():
            shutil.copyfile(examples / name, root / name)
    path = root / "company.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _audit_entries(root: Path, action_id: str) -> list[dict[str, Any]]:
    log = root / ".audit" / "workspace.log"
    if not log.exists():
        return []
    entries = []
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("action_id") == action_id:
            entries.append(entry)
    return entries


def _steps(root: Path, out: Callable[[str], None]) -> dict[str, Any]:
    import config_loader
    import connection_health
    import state_db
    from action_execution import run_approved
    from approval_signing import GatewaySigner
    from providers.sample_google_workspace import NAMED_FIGURE, NAMED_SENDER
    from workspace_client import get_workspace_client

    report: dict[str, Any] = {"ok": False, "steps": {}}

    def step(n: int, title: str) -> None:
        out("")
        out(f"Step {n}/7 — {title}")
        out("─" * 70)

    cfg_path = _prepare(root)
    config = config_loader.load_config(cfg_path)
    signer = GatewaySigner.generate()
    signer.enrol(config)
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)

    # 1 — Connection health -------------------------------------------------
    step(1, "Connection health (sample provider, no credentials)")
    health = connection_health.connection_report(config)
    report["steps"]["health"] = health
    assert health["healthy"] and health["mode"] == "sample", f"health not green: {health}"
    out("Provider sample_google: healthy, sample mode. No real account connected.")

    client = get_workspace_client(config)

    # 2 — Morning brief through the daily loop's collectors ------------------
    step(2, "Morning brief — four parts, read through the connection")
    import briefing_sources
    import daily_briefing

    window = client.calendar_list(
        datetime.combine(today, time(0, 0), tz).isoformat(),
        datetime.combine(tomorrow, time(23, 59), tz).isoformat(),
    )
    schedule_titles = [e["summary"] for e in window]
    assert any("Sarah Linden" in t for t in schedule_titles), schedule_titles
    assert any("Marcus Bell" in t for t in schedule_titles), schedule_titles
    assert any("Aisha Rahman" in t for t in schedule_titles), schedule_titles
    collector_events = briefing_sources.collect_calendar_summary(config, hours_ahead=48)
    out(f"Schedule (today + tomorrow): {len(window)} events; daily-loop collector saw "
        f"{len(collector_events)} in the next 48h.")

    gmail_items = daily_briefing.collect_gmail(config, root)
    entries = gmail_items.get("items", gmail_items) if isinstance(gmail_items, dict) else gmail_items
    brief_subjects = []
    for entry in entries:
        for msg in entry.get("result", []):
            brief_subjects.append(str(msg.get("subject", "")))
    assert any("URGENT" in s for s in brief_subjects), brief_subjects
    assert any("2ct lab-grown parcel" in s for s in brief_subjects), brief_subjects
    out(f"Unread email needing attention: {len(brief_subjects)} message(s) across the brief queries.")

    import demo_flow
    deal = demo_flow._follow_up(root)
    follow_up = f"{deal['contact_name']} ({deal['client_name']})"
    out(f"Pending follow-up: {follow_up} — proposal waiting since the pipeline went stale.")
    report["steps"]["brief"] = {
        "schedule": schedule_titles,
        "email_subjects": brief_subjects,
        "follow_up": follow_up,
    }

    # 3 — One named email, in full -------------------------------------------
    step(3, "One named email, fetched in full")
    named = client.mail_search(f"from:{NAMED_SENDER}")
    assert len(named) == 1, named
    assert NAMED_FIGURE in named[0]["body"], named[0]["body"]
    out(f"From {named[0]['sender']}: figure in the body checks out ({NAMED_FIGURE}).")
    report["steps"]["named_email"] = {"subject": named[0]["subject"], "figure": NAMED_FIGURE}

    # 4 — Draft reply into the Review Queue (unsent) --------------------------
    step(4, "Draft reply — queued and previewed, nothing sent")
    import review_queue
    from action_risk import get_action_risk

    payload = {
        "to": NAMED_SENDER,
        "subject": "Re: Quote for the 2ct lab-grown parcel",
        "body": "Hi Daniel, Thursday works for us. We will deliver by Thursday morning. — Moe J Diamonds",
    }
    action = state_db.create_pending_action(
        config, "gmail.send", "sample_google", NAMED_SENDER, payload,
        summary="Reply to Daniel Okafor: Thursday delivery works",
    )
    assert action, "draft action was not created"
    preview = {
        "to": NAMED_SENDER,
        "subject": payload["subject"],
        "risk": get_action_risk("gmail.send"),
        "expected_effect": review_queue._expected_effect("gmail.send", NAMED_SENDER, payload),
        "reversal": review_queue._reversal_hint("gmail.send"),
    }
    pending = briefing_sources.collect_pending_actions(config)
    assert any(p.get("action_id") == action["id"] for p in pending), "draft missing from pending approvals"
    assert client.sent == [], "an unapproved draft was sent"
    out(f"Queued action {action['id']}: {preview['expected_effect']}")
    out(f"Risk: {preview['risk']}. Reversal: {preview['reversal']}")
    out("Pending approvals in the brief now include this draft. Outbox is still empty.")
    report["steps"]["draft"] = {"action_id": action["id"], "preview": preview, "sent_before_approval": 0}

    # 5 — Approve (signed) and execute through the guarded path ---------------
    step(5, "Approve and execute — once, and only once")
    signature = signer.sign_decision(state_db.get_pending_action(config, action["id"]), "approve", APPROVER)
    approved = state_db.approve_pending_action(
        config, action["id"], approver=APPROVER, reason="wp5 flow", signature=signature)
    assert approved, "approval was refused"
    result = run_approved(
        config, state_db.get_pending_action(config, action["id"]), client,
        lambda c, a: c.mail_send(**{k: a["payload"][k] for k in ("to", "subject", "body")}),
    )
    assert result.ok, f"execution failed at {result.stage}: {result.error}"
    assert len(client.sent) == 1, client.sent
    again = run_approved(
        config, state_db.get_pending_action(config, action["id"]), client,
        lambda c, a: c.mail_send(**{k: a["payload"][k] for k in ("to", "subject", "body")}),
    )
    assert not again.ok and len(client.sent) == 1, "a second execution sent a duplicate"
    out(f"Sent once (sample outbox). Second attempt refused at '{again.stage}' — no duplicate.")
    report["steps"]["send"] = {"action_id": action["id"], "outbox": len(client.sent),
                               "second_attempt_stage": again.stage}

    # 6 — Approved calendar move ----------------------------------------------
    step(6, "Move the 3 PM appointment to tomorrow at 4:30 PM")
    event = next(e for e in window if e["id"] == "sample-evt-appointment")
    new_start = datetime.combine(tomorrow, time(16, 30), tz).isoformat()
    new_end = datetime.combine(tomorrow, time(17, 30), tz).isoformat()
    move = state_db.create_pending_action(
        config, "calendar.update", "sample_google", event["id"],
        {"event_id": event["id"], "start": new_start, "end": new_end},
        summary=f"Move '{event['summary']}' from today 3:00 PM to tomorrow 4:30 PM",
    )
    assert move, "move action was not created"
    sig2 = signer.sign_decision(state_db.get_pending_action(config, move["id"]), "approve", APPROVER)
    assert state_db.approve_pending_action(
        config, move["id"], approver=APPROVER, reason="wp5 flow", signature=sig2), "move approval refused"
    moved = run_approved(
        config, state_db.get_pending_action(config, move["id"]), client,
        lambda c, a: c.calendar_update(a["payload"]["event_id"],
                                       start=a["payload"]["start"], end=a["payload"]["end"]),
    )
    assert moved.ok, f"move failed at {moved.stage}: {moved.error}"
    after = client.calendar_list(
        datetime.combine(today, time(0, 0), tz).isoformat(),
        datetime.combine(tomorrow, time(23, 59), tz).isoformat(),
    )
    copies = [e for e in after if e["id"] == event["id"]]
    assert len(copies) == 1 and copies[0]["start"] == new_start, copies
    assert not any(e["id"] == event["id"] and e["start"] == event["start"] for e in after)
    out(f"'{event['summary']}' now starts {new_start}; the old slot is empty; exactly one copy exists.")
    report["steps"]["move"] = {"action_id": move["id"], "new_start": new_start, "copies": len(copies)}

    # 7 — Audit ----------------------------------------------------------------
    step(7, "Audit — both actions on the record")
    audit = {}
    for label, action_id in (("send", action["id"]), ("move", move["id"])):
        entries_for = _audit_entries(root, action_id)
        final = state_db.get_pending_action(config, action_id)
        audit[label] = {"entries": len(entries_for), "final_state": final["state"]}
        assert final["state"] == "executed", audit[label]
        assert entries_for, f"no audit entries for {label}"
        out(f"{label}: state executed, {len(entries_for)} audit entries, approver {final.get('approver')}.")
    report["steps"]["audit"] = audit

    report["ok"] = True
    out("")
    out("── End of WP5 flow ──")
    return report


def run_wp5_flow(out: Callable[[str], None] = print, keep: Path | str | None = None) -> int:
    """Run the WP5 flow. Returns 0 on success, 1 on a failed check."""
    import demo_flow

    if keep is not None:
        root = Path(keep).expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            why = demo_flow._owned_by_demo(root)
            if why:
                out(f"Refusing: {why}. Pick a new directory, or remove it yourself.")
                return 2
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        (root / MARKER).write_text(demo_flow.MARKER_TEXT)
        with demo_flow._isolated_env(root / ".hermes-demo"):
            try:
                _steps(root, out)
            except AssertionError as exc:
                out(f"WP5 flow FAILED: {exc}")
                return 1
        out(f"WP5 state kept in {root}.")
        return 0
    with tempfile.TemporaryDirectory(prefix="wp5-flow-") as tmp:
        root = Path(tmp)
        with demo_flow._isolated_env(root / ".hermes-demo"):
            try:
                _steps(root, out)
            except AssertionError as exc:
                out(f"WP5 flow FAILED: {exc}")
                return 1
    out("WP5 state discarded. Run it again for a fresh start.")
    return 0


def _main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="WP5 connection flow (sample data)")
    parser.add_argument("--keep", help="Keep the throwaway workspace in this directory")
    args = parser.parse_args(argv)
    return run_wp5_flow(keep=args.keep)


if __name__ == "__main__":
    sys.exit(_main())
