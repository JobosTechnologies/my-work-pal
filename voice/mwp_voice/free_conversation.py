#!/usr/bin/env python3
"""Free conversation mode: a real LLM talks; the foundation stays guarded.

The scripted engine (conversation.py) walks a call through fixed steps.
This module is the other mode: an LLM converses freely — listening,
following the owner's intent, changing subject when the owner does — and
the foundation bridge is exposed to it as tools (brief, queue, propose,
execute, audit, cancel).

The approval separation is unchanged, and it does not rest on the LLM's
good behaviour:

* The tools can read, propose, cancel, and execute. None of them can
  approve. ``execute`` refuses unless the action's state is ``approved``.
* Approval still comes only from :class:`ApprovalGateway`, and the only
  road to it here is :meth:`FreeCallState.note_owner_utterance` — worker
  code, not a tool. The worker feeds it the owner's actual transcribed
  words; it runs the same deterministic decision vocabulary as the
  scripted engine (:func:`conversation.classify_decision`), and only a
  clear yes, spoken while a read-back is outstanding, is handed to the
  gateway. The LLM cannot call it, name it, or fake its input.
* The system prompt carries the conduct (confirm the owner before sharing
  anything, read back recipient + subject before any send, a clear yes or
  it does not count) as how the assistant carries itself — guidance for
  the conversation, never the enforcement. Enforcement is the state
  check above, exactly as in scripted mode.
"""
from __future__ import annotations

from typing import Any

from .conversation import classify_decision
from .foundation_bridge import ApprovalRefused, SpokenPreview, VoiceBridge


def build_instructions(
    *, assistant_name: str, business_name: str, owner_name: str
) -> str:
    """The free-mode system prompt: conduct, not steps."""
    return f"""You are {assistant_name}, the voice assistant for {business_name}, \
speaking on a phone call. Talk like a person: listen first, understand what \
the caller means, and answer naturally and briefly, the way a good chief of \
staff would. There is no script and no fixed order. If the caller changes \
the subject, follow them. If you do not know something, say so plainly. \
Never invent figures, names, dates, or results: if a tool did not tell you, \
you do not know it.

How you carry yourself:

- You open by introducing yourself as {assistant_name} from {business_name} \
and checking that you are speaking with {owner_name}. Until the caller \
confirms they are the owner, share nothing about the business: no brief, no \
figures, no names, no queue items.
- Keep answers short enough for a phone call: one idea at a time, plain \
words, no lists read out loud unless the caller asks.
- You can read the morning brief and the review queue, propose an email, \
and run an approved action. Proposing never sends anything; a proposed \
email simply waits in the queue.
- Before anything is sent, read it back in full: who it goes to, the \
subject, and what it says. Then ask plainly whether to send it. Only a \
clear yes counts. If the answer is vague, unclear, or about something \
else, it is not a yes: ask again, simply. If they say no, leave the item \
waiting and move on.
- After a clear yes, call the execute tool for that action. If the tool \
says the action is not approved, do not argue and do not retry: tell the \
caller it has not gone out, and ask again.
- If the caller asks why something happened, check the audit trail and \
answer from it, not from memory.
- If the caller goes quiet, remind them once, gently, that you are still \
there. If there is still no answer, wrap up warmly and end the call \
rather than holding an open line."""


class FreeCallState:
    """Worker-side state for one free-mode call.

    Holds the bridge and the one piece of conversational state the safety
    path needs: which action, if any, has been read back and is waiting
    for the owner's decision. The agent's tools delegate here; the
    approval hook (:meth:`note_owner_utterance`) is deliberately not a
    tool and is unreachable from the LLM.
    """

    def __init__(self, bridge: VoiceBridge, *, owner_name: str = "the owner") -> None:
        self._bridge = bridge
        self.owner_name = owner_name
        self.pending_readback_id: str | None = None

    # -- the approval hook (worker code only; never a tool) ----------------

    def note_owner_utterance(self, text: str) -> str | None:
        """Feed one transcribed owner utterance to the approval path.

        Returns the approved action's id when the utterance is a clear yes
        to the outstanding read-back (the gateway has signed it and the
        action is now ``approved``); otherwise returns None. A clear no
        clears the outstanding read-back and leaves the item waiting.
        """
        action_id = self.pending_readback_id
        if action_id is None:
            return None
        decision = classify_decision(text)
        if decision == "clear_no":
            self.pending_readback_id = None
            return None
        if decision != "clear_yes":
            return None
        try:
            self._bridge.gateway.confirm(action_id, "clear_yes", self.owner_name)
        except ApprovalRefused:
            return None
        self.pending_readback_id = None
        return action_id

    # -- tools (these are what the LLM can call) ----------------------------

    def morning_brief(self) -> str:
        return self._bridge.morning_brief()

    def review_queue(self) -> str:
        items = self._bridge.queue_items()
        if not items:
            return "The review queue is empty: nothing is waiting for a decision."
        lines = [f"{len(items)} item(s) waiting for a decision:"]
        for item in items:
            lines.append(
                f"- id {item.action_id}: {item.summary} "
                f"(risk: {item.risk_level})"
            )
        return "\n".join(lines)

    def propose_email(self, to: str, subject: str, body: str) -> str:
        action = self._bridge.propose_email(to, subject, body)
        action_id = str(action["id"])
        self.pending_readback_id = action_id
        preview = self._preview(action_id)
        readback = (
            preview.readback()
            if preview is not None
            else f"To confirm: I will send an email to {to}, with the subject '{subject}'. The message says: {body}"
        )
        return (
            f"Proposed, not sent. Action id {action_id} is waiting in the "
            f"review queue. Read this back to the owner and ask for a clear "
            f"yes before anything is sent:\n{readback}"
        )

    def read_back(self, action_id: str) -> str:
        preview = self._preview(action_id)
        if preview is None:
            return f"No waiting action with id {action_id}."
        self.pending_readback_id = action_id
        return (
            "Read this back to the owner and ask for a clear yes:\n"
            + preview.readback()
        )

    def execute(self, action_id: str) -> str:
        state = self._bridge.action_state(action_id)
        if state is None:
            return f"No such action: {action_id}. Nothing was sent."
        if state != "approved":
            return (
                f"Action {action_id} is {state}, not approved, so nothing "
                "was sent. Read it back and wait for a clear yes first."
            )
        outcome = self._bridge.execute(action_id)
        if outcome.ok:
            return f"Done: action {action_id} executed. {outcome.message}"
        return f"Action {action_id} did not go out ({outcome.stage}): {outcome.message}"

    def cancel(self, action_id: str) -> str:
        if self.pending_readback_id == action_id:
            self.pending_readback_id = None
        if self._bridge.cancel(action_id):
            return f"Action {action_id} is cancelled. It can never be sent now."
        state = self._bridge.action_state(action_id)
        return f"Action {action_id} could not be cancelled (state: {state})."

    def audit_lookup(self, action_id: str) -> str:
        entries = self._bridge.audit_trail(action_id)
        if not entries:
            return f"No audit entries for action {action_id}."
        lines = [f"Audit trail for action {action_id}:"]
        for entry in entries:
            status = entry.get("status", "?")
            detail = entry.get("detail") or entry.get("reason") or ""
            lines.append(f"- {status}: {detail}".rstrip(": "))
        return "\n".join(lines)

    # -- internals ----------------------------------------------------------

    def _preview(self, action_id: str) -> SpokenPreview | None:
        for item in self._bridge.queue_items():
            if item.action_id == action_id:
                return item
        return None
