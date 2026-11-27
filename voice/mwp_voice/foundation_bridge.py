#!/usr/bin/env python3
"""The bridge between the voice layer and the Chief of Staff foundation.

Everything the voice layer is allowed to do with the foundation goes through
this module, and the permissions are deliberately uneven:

* Reading is free: the morning brief (the foundation's read-only daily loop),
  the Review Queue items, their previews, and the audit log.
* Cancelling a still-pending item is allowed: it can only stop work.
* Approving is NOT a bridge method the conversation layer can reach. A spoken
  decision becomes an approval only inside :class:`ApprovalGateway`, which
  holds the gateway signing key (the same separation the foundation enforces:
  the agent's own tools are read-only and cannot approve; only the human
  approves, and the approval is signed). The conversation engine never sees
  the signer. The call driver (in production, the part of the worker that
  owns the human channel; in tests, the mock driver) hands the gateway a
  decision record, and the gateway signs only a clear yes, only for an
  action still waiting in ``requested``.
* Executing runs only on an action the gateway has already approved, through
  the foundation's own guarded path (``action_execution.run_approved``:
  claim, account check, capability check, single-use grant, record).

Sample mode reuses the foundation demo's own workspace preparation
(``demo_flow``) so the voice layer is exercised against exactly the data and
code path the flagship demo proves, with a stand-in mailbox: no mail leaves
the machine and no credentials exist anywhere in this module.
"""
from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VOICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _VOICE_DIR.parents[1]
_SCRIPTS = _REPO_ROOT / "shared" / "scripts"
_BRIEFING_SCRIPTS = _REPO_ROOT / "skills" / "daily-briefing" / "scripts"
for _p in (_SCRIPTS, _BRIEFING_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class ApprovalRefused(RuntimeError):
    """The gateway would not turn a decision record into a signed approval."""


@dataclass
class SpokenPreview:
    """One Review Queue item, shaped for speaking: what, why, risk, reversal."""

    action_id: str
    summary: str
    what: str
    why: str
    risk_level: str
    risk_text: str
    reversal: str
    to: str | None
    subject: str | None
    body: str

    def readback(self) -> str:
        """The hard read-back line: recipient and subject, before any yes."""
        return (
            f"To confirm: I will send an email to {self.to}, "
            f"with the subject '{self.subject}'. The message says: {self.body}"
        )


@dataclass
class ExecutionOutcome:
    ok: bool
    stage: str
    message: str
    message_id: str | None = None


class ApprovalGateway:
    """The human side of approval, and the only holder of the signing key.

    Constructed by the call's owner channel (the worker's gateway role, or
    the mock driver in tests), never by the conversation engine. ``confirm``
    is the single door from speech to a signed approval, and it is narrow on
    purpose: the decision must be a clear yes, and the action must still be
    waiting in ``requested``.
    """

    def __init__(self, config: Any, signer: Any) -> None:
        self._config = config
        self._signer = signer

    def confirm(self, action_id: str, decision: str, approver: str) -> dict[str, Any]:
        import state_db

        if decision != "clear_yes":
            raise ApprovalRefused(f"decision is {decision!r}, not a clear yes")
        action = state_db.get_pending_action(self._config, action_id)
        if action is None:
            raise ApprovalRefused(f"no such action: {action_id}")
        if action.get("state") != "requested":
            raise ApprovalRefused(
                f"action {action_id} is {action.get('state')!r}, not waiting for approval"
            )
        signature = self._signer.sign_decision(action, "approve", approver)
        approved = state_db.approve_pending_action(
            self._config,
            action_id,
            approver=approver,
            reason="voice call: read-back given, owner said yes",
            signature=signature,
        )
        if not approved:
            raise ApprovalRefused(f"foundation refused approval of {action_id}")
        return approved


class VoiceBridge:
    """Read, cancel, execute-approved: the voice layer's foundation access."""

    def __init__(self, config: Any, root: Path, cfg_path: Path, stack: ExitStack) -> None:
        self._config = config
        self._root = root
        self._cfg_path = cfg_path
        self._stack = stack
        import demo_flow

        self._mailbox = demo_flow._demo_mailbox_class()(config)

    # -- construction ------------------------------------------------------

    @classmethod
    def sample(cls, root: Path | str) -> "VoiceBridge":
        """A bridge over a throwaway sample workspace (the demo's own data)."""
        import config_loader
        import demo_flow
        from approval_signing import GatewaySigner

        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        stack = ExitStack()
        stack.enter_context(demo_flow._isolated_env(root / ".hermes-demo"))
        try:
            cfg_path = demo_flow._prepare(root)
            config = config_loader.load_config(cfg_path)
            signer = GatewaySigner.generate()
            signer.enrol(config)
        except Exception:
            stack.close()
            raise
        bridge = cls(config, root, cfg_path, stack)
        bridge._gateway = ApprovalGateway(config, signer)
        return bridge

    def close(self) -> None:
        self._stack.close()

    def __enter__(self) -> "VoiceBridge":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def gateway(self) -> ApprovalGateway:
        """The approval door. Hand this to the call driver, not the engine."""
        return self._gateway

    @property
    def sent(self) -> list[dict[str, Any]]:
        """Messages the stand-in mailbox has 'sent' (nothing leaves the machine)."""
        return list(self._mailbox.sent)

    # -- reading -----------------------------------------------------------

    def morning_brief(self) -> str:
        """The foundation's daily-loop brief, rendered as text (read-only)."""
        import demo_flow

        return demo_flow._briefing(self._cfg_path)

    def queue_items(self) -> list[SpokenPreview]:
        """Review Queue items still waiting for the owner, oldest first."""
        import review_queue
        import state_db
        from action_risk import get_action_risk, get_risk_explanation

        items: list[SpokenPreview] = []
        actions = state_db.list_pending_actions(
            self._config, state="requested", include_expired=False
        )
        for action in actions:
            fresh = state_db.get_pending_action(self._config, action["id"]) or action
            payload = fresh.get("payload") or {}
            action_type = str(fresh.get("type") or fresh.get("action_type") or "")
            item = review_queue._pending_action_to_item(fresh)
            risk_level = get_action_risk(action_type)
            items.append(
                SpokenPreview(
                    action_id=str(fresh["id"]),
                    summary=str(fresh.get("summary") or item["summary"]),
                    what=review_queue._expected_effect(
                        action_type, str(fresh.get("target") or ""), payload
                    ),
                    why=str(item["why"]),
                    risk_level=risk_level,
                    risk_text=(
                        f"{risk_level} — {get_risk_explanation(action_type, risk_level)}"
                    ),
                    reversal=review_queue._reversal_hint(action_type),
                    to=payload.get("to"),
                    subject=payload.get("subject"),
                    body=str(payload.get("body") or ""),
                )
            )
        return items

    def queue_summary(self) -> dict[str, Any]:
        import state_db

        return state_db.get_pending_summary(self._config)

    def action_state(self, action_id: str) -> str | None:
        import state_db

        action = state_db.get_pending_action(self._config, action_id)
        return None if action is None else str(action.get("state"))

    def audit_trail(self, action_id: str) -> list[dict[str, Any]]:
        """This action's entries from the foundation audit log, in order."""
        log = self._root / ".audit" / "workspace.log"
        if not log.exists():
            return []
        entries = []
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("action_id") == action_id:
                entries.append(entry)
        return entries

    # -- proposing (the agent side; sends nothing) -------------------------

    def seed_follow_up(self) -> dict[str, Any]:
        """Queue the sample follow-up proposal, as the demo's step 2 does."""
        import demo_flow
        import state_db

        deal = demo_flow._follow_up(self._root)
        subject = f"Following up: {deal['client_name']} proposal"
        return state_db.create_pending_action(
            self._config,
            "gmail.send",
            "demo",
            deal["contact_email"],
            {
                "to": deal["contact_email"],
                "subject": subject,
                "body": (
                    f"Hi {deal.get('contact_name', '')}, checking in on the "
                    "proposal. Happy to answer questions."
                ),
            },
            summary=f"Follow up with {deal['contact_name']} ({deal['client_name']})",
        )

    def propose_email(
        self, to: str, subject: str, body: str, summary: str | None = None
    ) -> dict[str, Any]:
        """Queue an email as a pending action in ``requested`` (sends nothing).

        The general form of :meth:`seed_follow_up`: the action waits in the
        Review Queue until the gateway signs a clear yes and :meth:`execute`
        runs it through the guarded path.
        """
        import state_db

        return state_db.create_pending_action(
            self._config,
            "gmail.send",
            "demo",
            to,
            {"to": to, "subject": subject, "body": body},
            summary=summary or f"Send email to {to}: {subject}",
        )

    # -- acting (only ever on gateway-approved actions) --------------------

    def execute(self, action_id: str) -> ExecutionOutcome:
        """Run one approved action through the foundation's guarded path."""
        import state_db
        from action_execution import run_approved

        action = state_db.get_pending_action(self._config, action_id)
        if action is None:
            return ExecutionOutcome(False, "missing", f"No such action: {action_id}")
        if action.get("state") != "approved":
            return ExecutionOutcome(
                False,
                "not_approved",
                f"Action is {action.get('state')!r}; only an approved action can run.",
            )
        result = run_approved(
            self._config,
            action,
            self._mailbox,
            lambda client, a: client.mail_send(
                **{k: a["payload"][k] for k in ("to", "subject", "body")}
            ),
        )
        if not result.ok:
            return ExecutionOutcome(False, str(result.stage), str(result.error))
        data = (result.result or {}).get("data") or {}
        return ExecutionOutcome(
            True, "executed", "Sent through the guarded path.", data.get("message_id")
        )

    def cancel(self, action_id: str, reason: str = "cancelled by owner on voice call") -> bool:
        """Cancel a still-pending action. Cancelled work can never execute."""
        import state_db

        if self.action_state(action_id) not in ("requested", "approved"):
            return False
        cancelled = state_db.cancel_pending_action(self._config, action_id, reason=reason)
        return bool(cancelled) and self.action_state(action_id) == "cancelled"
