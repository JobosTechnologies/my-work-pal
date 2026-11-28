#!/usr/bin/env python3
"""The conversation layer: how a My Work Pal call actually talks.

Transport-agnostic on purpose. The engine consumes one owner utterance (text,
from speech-to-text or from the mock provider) and produces the assistant's
next lines plus events. LiveKit owns the audio: barge-in, turn detection, and
silence timing live there (see livekit_worker.py); this layer owns intent,
context, and repairs, and it is the layer the spoken scenarios judge.

Hard rules, from the Phase 1 plan (WP3 and WP4):

* The engine can propose, preview, and read back. It cannot approve. When the
  owner gives a clear yes, the engine emits a ``decision_made`` event and
  stops; turning that into a signed approval is the ApprovalGateway's job,
  and execution happens only when the driver hands the gateway's approved
  action back to :meth:`ConversationEngine.on_approved`.
* A yes must be clear. Silence, an unclear sound, or an answer to a
  different question is not a yes: the engine asks again, and after two
  unclear replies it leaves the item open in the queue and moves on. Nothing
  is ever approved from a guess.
* "Say that again" repeats the last thing said: same words, shorter
  sentences, nothing added or changed.
* "The other one" offers the other waiting item and starts its read-back
  fresh. Nothing carries over between items.
* "Stop the task" / "forget that" cancels the pending work at once, through
  the bridge, and cancelled work cannot execute afterwards.
* "Why did you do that?" is answered from the audit trail, not from memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .foundation_bridge import SpokenPreview, VoiceBridge

# -- spoken decision vocabulary -------------------------------------------
# Deliberately small. A decision phrase must be the whole utterance or
# LEAD it: speech-to-text returns whole sentences, so an owner who says
# "yes, please send the email to John now" has given a clear yes even
# though the sentence is longer than the vocabulary entry. A yes that
# does not lead ("I said yes earlier, but not now") is not a yes, and a
# negation or hold word anywhere after the lead voids it ("yes, but
# don't send it yet"). Anything else is "unclear", which is the safe
# direction: unclear never approves.

_CLEAR_YES = {
    "yes", "yeah", "yep", "yes please", "yes go ahead", "go ahead",
    "yes do it", "do it", "yes send it", "send it", "approve", "approved",
    "yes approve it", "confirm", "yes confirm", "okay yes", "ok yes",
}
_CLEAR_NO = {
    "no", "nope", "no don't", "don't", "do not", "not now", "no thanks",
    "leave it", "no leave it", "skip it", "no skip it",
}
_REPEAT = {
    "say that again", "say it again", "repeat that", "repeat", "what was that",
    "pardon", "come again", "can you repeat that",
}
_OTHER = {"the other one", "no the other one", "the other", "other one"}
_STOP = {
    "stop the task", "stop", "forget that", "forget it", "cancel the task",
    "cancel that", "cancel it",
}
_NEXT = {"next", "next item", "next one", "move on", "go on"}
_BAD_TIME = {"it's a bad time", "bad time", "call me later", "this is a bad time"}


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[.,!?;:'\"]+", "", text)
    return re.sub(r"\s+", " ", text)


# Words that void a leading yes: the owner qualified or reversed it.
_YES_VOIDERS = {"no", "not", "dont", "never", "wait", "stop", "cancel", "hold", "back"}
# Words that void a leading no, symmetrically.
_NO_VOIDERS = {"yes", "yeah", "yep"}


def _led_by(norm: str, phrases: set[str]) -> str | None:
    """The remainder when ``norm`` is led by a phrase, else None.

    Longest phrase wins, so "yes go ahead" beats "yes". An exact match
    returns the empty remainder.
    """
    for phrase in sorted(phrases, key=len, reverse=True):
        if norm == phrase:
            return ""
        if norm.startswith(phrase + " "):
            return norm[len(phrase) + 1:]
    return None


def classify_decision(text: str) -> str:
    """'clear_yes', 'clear_no', or 'unclear' — unclear is the safe default."""
    norm = _normalize(text)
    if norm in _CLEAR_YES:
        return "clear_yes"
    if norm in _CLEAR_NO:
        return "clear_no"
    rest = _led_by(norm, _CLEAR_YES)
    if rest is not None:
        return "unclear" if set(rest.split()) & _YES_VOIDERS else "clear_yes"
    rest = _led_by(norm, _CLEAR_NO)
    if rest is not None:
        return "unclear" if set(rest.split()) & _NO_VOIDERS else "clear_no"
    return "unclear"


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    say: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def _shorten(lines: list[str]) -> list[str]:
    """The same words, in shorter sentences (the 'say that again' repair).

    Splits sentences at commas and semicolons only, so the word multiset of
    the repetition is identical to the original: nothing added, nothing
    changed, nothing dropped.
    """
    out: list[str] = []
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            if not sentence:
                continue
            parts = [p.strip() for p in re.split(r"[,;]\s+", sentence) if p.strip()]
            for part in parts:
                part = part.rstrip(".!?")
                if part:
                    out.append(part[0].upper() + part[1:] + ".")
    return out


class ConversationEngine:
    """One call. Feed it utterances with :meth:`hear`; speak what it returns."""

    def __init__(
        self,
        bridge: VoiceBridge,
        *,
        assistant_name: str = "Pal",
        business_name: str = "the business",
        owner_name: str = "the owner",
    ) -> None:
        self._bridge = bridge
        self.assistant_name = assistant_name
        self.business_name = business_name
        self.owner_name = owner_name
        self.state = "new"  # new -> await_owner_confirm | brief -> queue -> await_decision -> done
        self.transcript: list[tuple[str, str]] = []
        self.pending_say: list[str] = []
        self._last_say: list[str] = []
        self._items: list[SpokenPreview] = []
        self._index = 0
        self._decision_attempts = 0
        self._unknown_streak = 0
        self._last_executed: SpokenPreview | None = None
        self._ended = False

    # -- call lifecycle ----------------------------------------------------

    def start(self, *, owner_confirmed: bool) -> TurnResult:
        """Open the call: identify, verify the owner, then brief and queue."""
        result = TurnResult()
        opening = (
            f"Hello, this is {self.assistant_name}, your assistant from "
            f"{self.business_name}."
        )
        if not owner_confirmed:
            self.state = "await_owner_confirm"
            result.say = [opening, f"Am I speaking with {self.owner_name}?"]
            return self._emit(result)
        result.say = [opening]
        result.say.extend(self._brief_and_queue())
        return self._emit(result)

    def interrupt(self) -> None:
        """Barge-in at the conversation layer: pending speech is dropped.

        LiveKit stops the audio; this drops the lines the engine had queued
        behind the current one, so the owner's new utterance is answered
        against what was actually heard, not what was planned.
        """
        self.pending_say = []
        self.transcript.append(("system", "(owner interrupted)"))

    def hear(self, text: str) -> TurnResult:
        """Process one owner utterance."""
        if self._ended:
            return TurnResult()
        self.transcript.append(("owner", text))
        result = TurnResult()
        norm = _normalize(text)

        # Repairs and global intents work in (almost) every state.
        if norm in _REPEAT and self._last_say:
            result.say = _shorten(self._last_say)
            return self._emit(result, keep_last=True)
        if norm in _STOP:
            return self._emit(self._handle_stop(result))
        if norm in _OTHER:
            return self._emit(self._handle_other(result))
        if norm.startswith("why did you"):
            return self._emit(self._handle_why(result))

        if self.state == "await_owner_confirm":
            return self._emit(self._handle_owner_confirm(text, result))
        if self.state == "await_decision":
            return self._emit(self._handle_decision(text, result))
        if (
            self.state == "done"
            and self._last_executed is not None
            and classify_decision(text) == "clear_yes"
        ):
            # The WP4 double-yes row, at the conversation layer: the second
            # yes finds the work already done and creates nothing.
            result.say = [
                "That is already done. It was sent once, and a second yes "
                "does not send it again."
            ]
            return self._emit(result)
        if norm in _NEXT:
            return self._emit(self._advance(result))
        if norm in _BAD_TIME:
            result.say = [
                "No problem. I will note that and end the call. "
                "Nothing has been changed."
            ]
            result.events.append(Event("bad_time", {}))
            self._end(result)
            return self._emit(result)
        return self._emit(self._handle_unknown(result))

    def on_approved(self, action_id: str) -> TurnResult:
        """The driver calls this with the gateway's approved action id.

        This is the only road to execution, and it starts from the gateway's
        approval, never from anything the engine heard. The bridge re-checks
        the action's state before the guarded path runs.
        """
        result = TurnResult()
        item = self._current()
        outcome = self._bridge.execute(action_id)
        if outcome.ok:
            self._last_executed = item
            result.say = [
                f"Done. The email to {item.to if item else 'the recipient'} "
                "has been sent, and it is recorded in the audit log."
            ]
            result.events.append(
                Event("executed", {"action_id": action_id, "message_id": outcome.message_id})
            )
        else:
            result.say = [
                "That action did not run. "
                f"The safety check stopped it at the {outcome.stage} step: {outcome.message}"
            ]
            result.events.append(
                Event("execution_refused", {"action_id": action_id, "stage": outcome.stage})
            )
        self._decision_attempts = 0
        result.say.extend(self._advance_lines())
        return self._emit(result)

    # -- state handlers ------------------------------------------------------

    def _handle_owner_confirm(self, text: str, result: TurnResult) -> TurnResult:
        decision = classify_decision(text)
        if decision == "clear_yes":
            self._unknown_streak = 0
            result.say = [f"Thank you, {self.owner_name}."]
            result.say.extend(self._brief_and_queue())
            return result
        if decision == "clear_no":
            # WP2 wrong-person rule: a short message, no private detail, end.
            result.say = [
                f"This is {self.assistant_name} from {self.business_name}. "
                "I will call back at a better time. Goodbye."
            ]
            result.events.append(Event("call_ended", {"reason": "not_the_owner"}))
            self._end(result)
            return result
        result.say = [
            f"I need to be sure I am speaking with {self.owner_name} "
            "before I share anything. Is that you? Please say yes or no."
        ]
        return result

    def _handle_decision(self, text: str, result: TurnResult) -> TurnResult:
        item = self._current()
        if item is None:
            self.state = "queue"
            return self._handle_unknown(result)
        decision = classify_decision(text)
        if decision == "clear_yes":
            self._decision_attempts = 0
            self._unknown_streak = 0
            result.say = ["Thank you. Recording your approval now."]
            result.events.append(
                Event(
                    "decision_made",
                    {
                        "action_id": item.action_id,
                        "decision": "clear_yes",
                        "approver": self.owner_name,
                    },
                )
            )
            return result
        if decision == "clear_no":
            self._decision_attempts = 0
            result.say = [
                "Understood. That one stays in your queue, unsent."
            ]
            result.events.append(
                Event(
                    "decision_made",
                    {"action_id": item.action_id, "decision": "clear_no"},
                )
            )
            result.say.extend(self._advance_lines())
            return result
        # Unclear is not a yes. Ask again; twice unclear and the item is
        # held open in the queue (WP3), never approved from a guess.
        self._decision_attempts += 1
        if self._decision_attempts >= 2:
            self._decision_attempts = 0
            result.say = [
                "I still do not have a clear yes or no, so I will leave "
                "this one open in your queue. Nothing has been sent."
            ]
            result.events.append(
                Event("held_open", {"action_id": item.action_id, "reason": "unclear_reply"})
            )
            result.say.extend(self._advance_lines())
            return result
        result.say = [
            "I want to be sure I heard you right. That was not a clear yes "
            "or no. " + item.readback() + " Say yes to approve, or no to leave it."
        ]
        return result

    def _handle_stop(self, result: TurnResult) -> TurnResult:
        item = self._current()
        if item is not None and self._bridge.action_state(item.action_id) in (
            "requested",
            "approved",
        ):
            self._bridge.cancel(item.action_id)
            result.say = [
                "Stopped. That task is cancelled, and it cannot send or "
                "run later. The cancellation is on the record."
            ]
            result.events.append(Event("task_cancelled", {"action_id": item.action_id}))
            self._decision_attempts = 0
            result.say.extend(self._advance_lines())
            return result
        if self._last_executed is not None:
            result.say = [
                "That one has already been sent, so it cannot be stopped. "
                f"To reverse it: {self._last_executed.reversal}"
            ]
            return result
        result.say = ["There is nothing pending to stop."]
        return result

    def _handle_other(self, result: TurnResult) -> TurnResult:
        others = [
            it for i, it in enumerate(self._items) if i != self._index
            and self._bridge.action_state(it.action_id) == "requested"
        ]
        if not others:
            result.say = ["There is only one item waiting, so there is no other one."]
            return result
        other = others[0]
        self._index = self._items.index(other)
        self._decision_attempts = 0
        result.say = ["Of course, the other one."]
        result.say.extend(self._present(other))
        return result

    def _handle_why(self, result: TurnResult) -> TurnResult:
        item = self._last_executed
        if item is None:
            result.say = ["I have not sent anything on this call yet."]
            return result
        trail = self._bridge.audit_trail(item.action_id)
        steps = ", ".join(
            f"{e.get('status')} at {e.get('timestamp')}" for e in trail
        )
        result.say = [
            f"From the audit log: you approved that on this call, after I "
            f"read back the recipient and subject. The record shows: {steps}."
        ]
        return result

    def _handle_unknown(self, result: TurnResult) -> TurnResult:
        """The misunderstanding ladder: ask plainly, offer meanings, hold open."""
        self._unknown_streak += 1
        if self._unknown_streak >= 2:
            self._unknown_streak = 0
            waiting = len(
                [i for i in self._items if self._bridge.action_state(i.action_id) == "requested"]
            )
            result.say = [
                "Let me summarise where we are, and we can pick this up "
                f"from the thread instead. {waiting} item(s) are still "
                "waiting in your queue, untouched. Nothing has been sent "
                "or changed by guessing."
            ]
            result.events.append(Event("held_open", {"reason": "not_understood"}))
            return result
        result.say = [
            "I did not understand that. You can say: next item, say that "
            "again, the other one, or stop the task."
        ]
        return result

    # -- queue walking -------------------------------------------------------

    def _brief_and_queue(self) -> list[str]:
        lines = ["Here is your morning brief."]
        brief = self._bridge.morning_brief().strip()
        if brief:
            lines.append(brief)
        self._items = self._bridge.queue_items()
        self._index = 0
        if not self._items:
            lines.append("Nothing is waiting for your approval.")
            self.state = "done"
            return lines
        lines.append(
            f"You have {len(self._items)} item(s) waiting for your approval."
        )
        lines.extend(self._present(self._items[0]))
        return lines

    def _present(self, item: SpokenPreview) -> list[str]:
        self.state = "await_decision"
        return [
            f"Item: {item.summary}.",
            item.what,
            f"Why: {item.why}",
            f"Risk: {item.risk_text}",
            f"Reversal: {item.reversal}",
            item.readback(),
            "Say yes to approve it, or no to leave it in your queue.",
        ]

    def _current(self) -> SpokenPreview | None:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def _advance(self, result: TurnResult) -> TurnResult:
        result.say.extend(self._advance_lines())
        return result

    def _advance_lines(self) -> list[str]:
        """Move to the next still-waiting item, or close the queue segment."""
        for nxt in range(self._index + 1, len(self._items)):
            candidate = self._items[nxt]
            if self._bridge.action_state(candidate.action_id) == "requested":
                self._index = nxt
                return ["Next item."] + self._present(candidate)
        self.state = "done"
        summary = self._bridge.queue_summary()
        waiting = summary["by_state"].get("requested", 0)
        executed = summary["by_state"].get("executed", 0)
        return [
            f"That is everything for now. {waiting} item(s) still waiting, "
            f"{executed} executed. Everything is on the record."
        ]

    # -- plumbing --------------------------------------------------------------

    def _end(self, result: TurnResult) -> None:
        self._ended = True
        self.state = "done"

    def _emit(self, result: TurnResult, *, keep_last: bool = False) -> TurnResult:
        for line in result.say:
            self.transcript.append(("assistant", line))
        if result.say and not keep_last:
            self._last_say = list(result.say)
        self.pending_say = list(result.say)
        return result
