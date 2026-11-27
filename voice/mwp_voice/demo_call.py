#!/usr/bin/env python3
"""A full mock phone call, printed: the WP2 first-stage proof.

Two scripted calls against the sample workspace, through the real engine,
bridge, gateway, and guarded execution path — with a stand-in mailbox, so
nothing leaves the machine:

Call 1 walks the whole loop: brief, queue preview, "say that again", an
unclear reply (asked again, not guessed), a clear yes, one execution, a
second yes that creates nothing, and "why did you do that?" answered from
the audit log.

Call 2 stops the task: the pending item is cancelled and can never run.

Run it with the foundation venv from the repo root:

    .venv/bin/python -m mwp_voice.demo_call        (with voice/ on PYTHONPATH)

Exits 0 only if every check in the calls passes.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable

_VOICE_DIR = Path(__file__).resolve().parent
if str(_VOICE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_VOICE_DIR.parent))

from mwp_voice.conversation import ConversationEngine  # noqa: E402
from mwp_voice.foundation_bridge import VoiceBridge  # noqa: E402
from mwp_voice.mock_provider import MockCallDriver  # noqa: E402


def _run_call(script: list[str], out: Callable[[str], None]) -> tuple[bool, VoiceBridge]:
    root = Path(tempfile.mkdtemp(prefix="mwp-demo-call-"))
    bridge = VoiceBridge.sample(root)
    action = bridge.seed_follow_up()
    engine = ConversationEngine(
        bridge, assistant_name="Pal", business_name="Sample Co", owner_name="Demo Owner"
    )
    driver = MockCallDriver(bridge, engine)
    record = driver.run(script, owner_confirmed=True)
    for speaker, text in record.transcript:
        out(f"{speaker.upper():>9}: {text}")
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        out(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    out("Checks:")
    if script and script[-1] == "Stop the task":
        check("nothing was sent", record.sent == [])
        check(
            "the item is cancelled",
            bridge.action_state(action["id"]) == "cancelled",
        )
    else:
        check("exactly one message sent", len(record.sent) == 1)
        statuses = [e.get("status") for e in record.audit.get(action["id"], [])]
        check(
            "audit shows requested -> approved -> executing -> executed",
            statuses == ["requested", "approved", "executing", "executed"],
        )
        check("queue afterwards: 1 executed, 0 waiting",
              record.queue_summary["by_state"].get("executed") == 1
              and record.queue_summary["by_state"].get("requested", 0) == 0)
    bridge.close()
    return ok, bridge


def run(out: Callable[[str], None] = print) -> int:
    out("Call 1 — brief, repair, approval, execution, audit")
    out("=" * 70)
    ok1, _ = _run_call(
        ["Say that again", "Hmm, maybe", "Yes, go ahead", "Yes", "Why did you do that?"],
        out,
    )
    out("")
    out("Call 2 — stop the task")
    out("=" * 70)
    ok2, _ = _run_call(["Stop the task"], out)
    out("")
    out("Demo call result: " + ("PASS" if ok1 and ok2 else "FAIL"))
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    raise SystemExit(run())
