"""WP2/WP3: the conversation engine, driven through full mock calls.

Every scenario runs the real engine, bridge, gateway, and guarded execution
path against sample data with a stand-in mailbox. The assertions are the
spoken-scenario conditions in miniature: read-back before approval, one
execution only, no approval from an unclear reply, repairs that change
nothing, and a stop that really stops.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared" / "scripts", ROOT / "skills" / "daily-briefing" / "scripts", ROOT / "voice"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import state_db  # noqa: E402
from mwp_voice.conversation import ConversationEngine, _shorten  # noqa: E402
from mwp_voice.foundation_bridge import VoiceBridge  # noqa: E402
from mwp_voice.mock_provider import MockCallDriver  # noqa: E402


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    with VoiceBridge.sample(tmp_path / "ws") as bridge:
        yield bridge


def _call(bridge, script, *, owner_confirmed=True):
    engine = ConversationEngine(
        bridge, assistant_name="Pal", business_name="Sample Co", owner_name="Demo Owner"
    )
    record = MockCallDriver(bridge, engine).run(script, owner_confirmed=owner_confirmed)
    return engine, record


def _words(text: str) -> Counter:
    # Alphanumeric tokens only: re-punctuation (the repair's whole point)
    # must not count as a word change.
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def test_full_call_brief_readback_approval_execution_audit(workspace):
    action = workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Yes, go ahead"])
    transcript = "\n".join(text for _who, text in record.transcript)
    # The read-back (recipient + subject) is spoken before the decision.
    assert action["payload"]["to"] in transcript
    assert action["payload"]["subject"] in transcript
    kinds = [e.kind for e in record.events]
    assert "decision_made" in kinds and "executed" in kinds
    assert len(record.sent) == 1
    statuses = [e.get("status") for e in record.audit[action["id"]]]
    assert statuses == ["requested", "approved", "executing", "executed"]


def test_engine_holds_no_approval_power(workspace):
    workspace.seed_follow_up()
    engine = ConversationEngine(workspace)
    assert not hasattr(engine, "gateway")
    assert not hasattr(engine, "signer")
    # Hearing a yes produces a decision event, not an approval: the action
    # is still only 'requested' until the gateway signs.
    engine.start(owner_confirmed=True)
    result = engine.hear("Yes, go ahead")
    assert [e.kind for e in result.events] == ["decision_made"]
    items = workspace.queue_items()
    assert items and workspace.action_state(items[0].action_id) == "requested"
    assert workspace.sent == []


def test_unclear_reply_is_asked_again_then_yes_works(workspace):
    workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Hmm, maybe", "Yes, go ahead"])
    transcript = "\n".join(text for _who, text in record.transcript)
    assert "not a clear yes or no" in transcript
    assert len(record.sent) == 1


def test_two_unclear_replies_hold_the_item_open_unsent(workspace):
    action = workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Hmm", "Whatever"])
    assert "held_open" in [e.kind for e in record.events]
    assert record.sent == []
    assert workspace.action_state(action["id"]) == "requested"


def test_clear_no_leaves_item_queued_unsent(workspace):
    action = workspace.seed_follow_up()
    _engine, record = _call(workspace, ["No"])
    assert record.sent == []
    assert workspace.action_state(action["id"]) == "requested"


def test_double_yes_sends_exactly_once(workspace):
    workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Yes, go ahead", "Yes"])
    assert len(record.sent) == 1
    transcript = "\n".join(text for _who, text in record.transcript)
    assert "already done" in transcript


def test_say_that_again_repeats_same_words_in_shorter_sentences():
    lines = [
        "To confirm: I will send an email to john@acme.example, with the "
        "subject 'Following up: Acme Corp proposal', today."
    ]
    repeated = _shorten(lines)
    assert _words(" ".join(repeated)) == _words(" ".join(lines))
    assert len(repeated) > len(lines)
    assert all(len(s) < len(lines[0]) for s in repeated)


def test_say_that_again_on_a_call_repeats_the_last_turn(workspace):
    workspace.seed_follow_up()
    engine = ConversationEngine(workspace)
    opening = engine.start(owner_confirmed=True)
    result = engine.hear("Say that again")
    assert _words(" ".join(result.say)) == _words(" ".join(opening.say))


def test_the_other_one_switches_item_and_approves_only_that_one(workspace):
    first = workspace.seed_follow_up()
    second = state_db.create_pending_action(
        workspace._config,
        "gmail.send",
        "demo",
        "jane@beta.example",
        {"to": "jane@beta.example", "subject": "Following up: Beta Ltd proposal",
         "body": "Hi Jane, checking in on the proposal."},
        summary="Follow up with Jane (Beta Ltd)",
    )
    _engine, record = _call(workspace, ["The other one", "Yes, go ahead"])
    assert len(record.sent) == 1
    assert record.sent[0]["to"] == "jane@beta.example"
    assert list(record.audit) == [second["id"]]
    assert workspace.action_state(first["id"]) == "requested"


def test_stop_the_task_cancels_and_nothing_can_run_after(workspace):
    action = workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Stop the task", "Yes, go ahead"])
    assert "task_cancelled" in [e.kind for e in record.events]
    assert record.sent == []
    assert workspace.action_state(action["id"]) == "cancelled"


def test_why_did_you_do_that_answers_from_the_audit_log(workspace):
    workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Yes, go ahead", "Why did you do that?"])
    transcript = "\n".join(text for _who, text in record.transcript)
    assert "audit log" in transcript
    assert "executed" in transcript


def test_wrong_person_gets_no_private_detail_and_the_call_ends(workspace):
    workspace.seed_follow_up()
    _engine, record = _call(workspace, ["No"], owner_confirmed=False)
    transcript = "\n".join(text for _who, text in record.transcript)
    assert "morning brief" not in transcript
    assert "call_ended" in [e.kind for e in record.events]
    assert record.sent == []


def test_owner_confirm_then_decline(workspace):
    workspace.seed_follow_up()
    _engine, record = _call(workspace, ["Yes", "No"], owner_confirmed=False)
    transcript = "\n".join(text for _who, text in record.transcript)
    assert "morning brief" in transcript
    assert record.sent == []


def test_misunderstanding_ladder_holds_work_open_without_guessing(workspace):
    # No seeded items: after the brief the call sits outside any decision,
    # so unknown utterances exercise the ask-again ladder itself.
    engine = ConversationEngine(workspace)
    engine.start(owner_confirmed=True)
    first = engine.hear("Blah blah")
    assert "did not understand" in " ".join(first.say)
    second = engine.hear("More nonsense")
    joined = " ".join(second.say)
    assert "summarise" in joined and "Nothing has been sent" in joined
    assert "held_open" in [e.kind for e in second.events]
    assert workspace.sent == []


def test_interrupt_drops_pending_speech(workspace):
    workspace.seed_follow_up()
    engine = ConversationEngine(workspace)
    engine.start(owner_confirmed=True)
    assert engine.pending_say
    engine.interrupt()
    assert engine.pending_say == []
