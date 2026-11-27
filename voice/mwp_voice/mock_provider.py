#!/usr/bin/env python3
"""The mock call driver: a whole phone call with no phone.

Following the mock-provider pattern (a dev provider that needs no network,
no number, and no account), this driver plays both ends of a call against
the real conversation engine, the real bridge, and the real approval
gateway:

* the assistant's lines go through the mock speech provider into a
  transcript;
* the owner's lines come from a script (a list of strings);
* when the engine reports a ``decision_made`` event with a clear yes, the
  driver — playing the gateway's side of the worker, the part that owns the
  human channel — hands the decision to the ApprovalGateway, and only on the
  gateway's approval does it call ``engine.on_approved``. A clear no never
  reaches the gateway. An unclear reply never becomes a decision event at
  all, so there is nothing for the gateway to sign.

The result is a CallRecord: the transcript, every event, what the stand-in
mailbox sent, and the audit trail — the evidence the spoken scenarios are
judged on, minus the audio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .conversation import ConversationEngine, Event
from .foundation_bridge import VoiceBridge
from .speech import MockSpeechProvider


@dataclass
class CallRecord:
    transcript: list[tuple[str, str]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    queue_summary: dict[str, Any] = field(default_factory=dict)


class MockCallDriver:
    """Drives one scripted call. The gateway is held here, not by the engine."""

    def __init__(
        self,
        bridge: VoiceBridge,
        engine: ConversationEngine,
        speech: MockSpeechProvider | None = None,
    ) -> None:
        self._bridge = bridge
        self._engine = engine
        self._speech = speech or MockSpeechProvider()
        self._events: list[Event] = []

    def _speak(self, lines: list[str]) -> None:
        for line in lines:
            self._speech.speak(line)

    def _absorb(self, result: Any) -> list[Event]:
        self._speak(result.say)
        self._events.extend(result.events)
        return list(result.events)

    def run(self, script: list[str], *, owner_confirmed: bool = True) -> CallRecord:
        """Run the call: opening, then one engine turn per scripted line."""
        self._absorb(self._engine.start(owner_confirmed=owner_confirmed))
        for owner_line in script:
            heard = self._speech.listen(owner_line)
            events = self._absorb(self._engine.hear(heard))
            for event in events:
                if event.kind != "decision_made":
                    continue
                if event.data.get("decision") != "clear_yes":
                    continue  # a no is final in the engine; nothing to sign
                approved = self._bridge.gateway.confirm(
                    event.data["action_id"],
                    "clear_yes",
                    event.data.get("approver", self._engine.owner_name),
                )
                self._absorb(self._engine.on_approved(approved["id"]))
        executed_ids = [
            e.data["action_id"] for e in self._events if e.kind == "executed"
        ]
        return CallRecord(
            transcript=list(self._engine.transcript),
            events=list(self._events),
            sent=self._bridge.sent,
            audit={aid: self._bridge.audit_trail(aid) for aid in executed_ids},
            queue_summary=self._bridge.queue_summary(),
        )
