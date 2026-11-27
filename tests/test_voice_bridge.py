"""WP2: the voice bridge — reading is free, approval is the gateway's alone.

The bridge is exercised against the foundation's own sample workspace (the
demo's data and code path), with the stand-in mailbox: nothing leaves the
machine and no credentials exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared" / "scripts", ROOT / "skills" / "daily-briefing" / "scripts", ROOT / "voice"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mwp_voice.foundation_bridge import ApprovalRefused, VoiceBridge  # noqa: E402


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    with VoiceBridge.sample(tmp_path / "ws") as br:
        yield br


def test_morning_brief_renders_from_sample_data(bridge):
    action = bridge.seed_follow_up()
    brief = bridge.morning_brief()
    assert len(brief) > 100
    assert action["payload"]["to"]  # the follow-up target exists in the data


def test_queue_preview_has_what_why_risk_reversal_and_readback(bridge):
    action = bridge.seed_follow_up()
    items = bridge.queue_items()
    assert len(items) == 1
    item = items[0]
    assert item.action_id == action["id"]
    assert item.to == action["payload"]["to"]
    assert item.subject == action["payload"]["subject"]
    assert item.what and item.why and item.risk_text and item.reversal
    readback = item.readback()
    assert item.to in readback and item.subject in readback


def test_execute_without_approval_is_refused_and_sends_nothing(bridge):
    action = bridge.seed_follow_up()
    outcome = bridge.execute(action["id"])
    assert not outcome.ok
    assert outcome.stage == "not_approved"
    assert bridge.sent == []
    assert bridge.action_state(action["id"]) == "requested"


def test_gateway_signs_only_a_clear_yes(bridge):
    action = bridge.seed_follow_up()
    for decision in ("clear_no", "unclear", "yes-ish", ""):
        with pytest.raises(ApprovalRefused):
            bridge.gateway.confirm(action["id"], decision, "owner")
    assert bridge.action_state(action["id"]) == "requested"
    approved = bridge.gateway.confirm(action["id"], "clear_yes", "owner")
    assert approved["state"] == "approved"
    assert approved.get("approval_signature")


def test_approved_action_executes_once_with_full_audit(bridge):
    action = bridge.seed_follow_up()
    bridge.gateway.confirm(action["id"], "clear_yes", "owner")
    outcome = bridge.execute(action["id"])
    assert outcome.ok and outcome.message_id == "demo-0001"
    assert len(bridge.sent) == 1
    statuses = [e.get("status") for e in bridge.audit_trail(action["id"])]
    assert statuses == ["requested", "approved", "executing", "executed"]
    # A second execution attempt finds the work done and is refused.
    again = bridge.execute(action["id"])
    assert not again.ok
    assert len(bridge.sent) == 1


def test_cancelled_action_cannot_be_approved_or_executed(bridge):
    action = bridge.seed_follow_up()
    assert bridge.cancel(action["id"])
    assert bridge.action_state(action["id"]) == "cancelled"
    with pytest.raises(ApprovalRefused):
        bridge.gateway.confirm(action["id"], "clear_yes", "owner")
    outcome = bridge.execute(action["id"])
    assert not outcome.ok
    assert bridge.sent == []
