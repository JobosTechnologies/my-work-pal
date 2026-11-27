#!/usr/bin/env python3
"""The LiveKit voice worker: the same engine, on a real call.

This is a thin transport shell around conversation.ConversationEngine,
written against LiveKit Agents 1.9 (Apache-2.0), the version pinned in
voice/requirements-voice.txt:

* LiveKit owns the audio: the session, turn-taking and interruption
  (barge-in) handling, and speech in and out. When the owner interrupts,
  the session stops the current speech itself; the engine's ``interrupt``
  hook is called so queued lines are dropped too.
* The conversation owns the talking, in one of two modes
  (``MWP_CONVERSATION_MODE``, see voice_config.py): ``scripted`` plugs
  the conversation engine in at ``llm_node`` (the engine owns the
  conversation: brief, queue, read-back, repairs; the gateway owns
  approval); ``free`` gives a real LLM the floor with the foundation
  bridge as tools, and approval still reaches the gateway only through
  worker code reading the owner's transcribed words
  (free_conversation.py). Either way this module holds the bridge's
  gateway (the human-channel side of the worker); the conversation
  layer never sees it.
* Speech is the latency dial: by default the session uses LiveKit's hosted
  inference speech models (fast, for judging call feel); the local setting
  (faster-whisper + Kokoro) attaches behind the same session shape in a
  later step. The speaking voice is part of the dial: LiveKit Inference
  selects it inside the TTS model string (``cartesia/sonic-3:{voice_id}``),
  resolved from ``MWP_TTS_MODEL`` / ``MWP_TTS_VOICE`` by voice_config.py —
  see docs/voice-options.md. The turn-detection model is LiveKit's own and
  sits under the separate LiveKit Model License, not Apache-2.0 — see
  voice/README.md.

Outbound calling (WP7): the scheduler (scheduler.py) still owns every
decision — when a call may be placed, quiet hours, retries, the urgent
limit. This module only turns a decision into a ringing phone, and only
when outbound is provisioned in the environment (voice_config.py):
``MWP_OUTBOUND_TRUNK_ID`` for a trunk that already exists in the LiveKit
project, or the carrier SIP settings (SignalWire) from which the worker
creates the trunk at start. With neither set, dialling raises a precise
"not provisioned" error, exactly as before WP7. Running this file
without livekit-agents installed, or without LIVEKIT_URL /
LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the environment, stops with a
precise message instead of a stack trace.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .conversation import ConversationEngine
from .foundation_bridge import VoiceBridge
from .free_conversation import FreeCallState, build_instructions
from .voice_config import (
    OUTBOUND_TRUNK_NAME,
    OutboundConfig,
    resolve_conversation_mode,
    resolve_llm_model,
    resolve_outbound_config,
    resolve_tts,
)


def _build_bridge() -> VoiceBridge:
    """Sample workspace for the first ring; a real owner workspace later."""
    root = Path(tempfile.mkdtemp(prefix="mwp-voice-"))
    bridge = VoiceBridge.sample(root)
    bridge.seed_follow_up()
    return bridge


class _EngineAgent:
    """Factory kept separate so the module imports without livekit present."""

    @staticmethod
    def build(bridge: VoiceBridge, engine: ConversationEngine) -> Any:
        from livekit.agents import Agent, llm

        class EngineLLM(llm.LLM):
            """A pipeline stand-in, never a brain.

            LiveKit's session refuses to run a turn without an LLM instance
            attached, but every reply here comes from the conversation
            engine: the agent's ``llm_node`` override answers each turn
            from the engine and never calls the model. If ``chat`` is ever
            reached, something has bypassed the engine, and failing loudly
            is the correct behaviour — this class must never invent speech.
            """

            @property
            def label(self) -> str:
                return "mwp-conversation-engine"

            @property
            def provider(self) -> str:
                return "my-work-pal"

            @property
            def model(self) -> str:
                return "conversation-engine"

            def chat(self, **kwargs: Any) -> Any:
                raise RuntimeError(
                    "the conversation engine is the brain; "
                    "no LLM generation is configured"
                )

        class MyWorkPalAgent(Agent):
            """The engine, plugged in at the pipeline's llm_node.

            llm_node is the one integration point every turn passes
            through — live STT turns and the framework's test runs alike:
            the latest owner message goes to the engine, and the engine's
            lines are yielded as the reply, so the session speaks exactly
            what the engine said and nothing else. A clear yes is handed
            to the gateway here (the worker's human-channel side); the
            engine itself never sees the gateway.
            """

            def __init__(self) -> None:
                super().__init__(
                    instructions=(
                        "You are the voice of My Work Pal. All replies come "
                        "from the conversation engine; never improvise an "
                        "action, a figure, or an approval."
                    ),
                    llm=EngineLLM(),
                )
                self._mwp_engine = engine
                self._mwp_bridge = bridge
                self._mwp_consumed: str | None = None

            async def on_enter(self) -> None:
                result = self._mwp_engine.start(owner_confirmed=False)
                for line in result.say:
                    await self.session.say(line, allow_interruptions=True)

            async def llm_node(self, chat_ctx: Any, tools: Any, model_settings: Any):  # type: ignore[override]
                engine = self._mwp_engine
                latest = None
                for item in reversed(chat_ctx.items):
                    if getattr(item, "role", None) == "user":
                        latest = item
                        break
                if latest is None or latest.id == self._mwp_consumed:
                    return  # no new owner turn: nothing to answer
                self._mwp_consumed = latest.id
                result = engine.hear(latest.text_content or "")
                lines = list(result.say)
                for event in result.events:
                    if event.kind == "decision_made" and event.data.get("decision") == "clear_yes":
                        approved = self._mwp_bridge.gateway.confirm(
                            event.data["action_id"],
                            "clear_yes",
                            event.data.get("approver", engine.owner_name),
                        )
                        lines.extend(engine.on_approved(approved["id"]).say)
                for line in lines:
                    yield line

        return MyWorkPalAgent()


class _FreeAgent:
    """Factory kept separate so the module imports without livekit present."""

    @staticmethod
    def build(
        bridge: VoiceBridge,
        state: FreeCallState,
        *,
        assistant_name: str,
        business_name: str,
        owner_name: str,
        llm: Any,
    ) -> Any:
        from livekit.agents import Agent, function_tool

        class MyWorkPalFreeAgent(Agent):
            """A real LLM converses; the bridge is its only reach.

            The tools delegate to FreeCallState (worker-side code). There
            is deliberately no approve tool: the gateway is reached only
            from the worker's transcription hook, which classifies the
            owner's actual words. ``llm`` is a LiveKit Inference model
            string in production, or a stand-in LLM in tests.
            """

            def __init__(self) -> None:
                super().__init__(
                    instructions=build_instructions(
                        assistant_name=assistant_name,
                        business_name=business_name,
                        owner_name=owner_name,
                    ),
                    llm=llm,
                )
                self._mwp_state = state

            async def on_enter(self) -> None:
                await self.session.say(
                    f"Hello, this is {assistant_name}, your assistant "
                    f"from {business_name}.",
                    allow_interruptions=True,
                )
                await self.session.say(
                    f"Am I speaking with {owner_name}?",
                    allow_interruptions=True,
                )

            @function_tool()
            async def morning_brief(self) -> str:
                """Read today's morning brief: schedule, inbox highlights, and anything waiting."""
                return self._mwp_state.morning_brief()

            @function_tool()
            async def review_queue(self) -> str:
                """List the actions waiting in the review queue, with their ids."""
                return self._mwp_state.review_queue()

            @function_tool()
            async def propose_email(self, to: str, subject: str, body: str) -> str:
                """Propose an email. This queues it for approval; it does NOT send anything."""
                return self._mwp_state.propose_email(to, subject, body)

            @function_tool()
            async def read_back(self, action_id: str) -> str:
                """Get the exact read-back text for a waiting action, to speak before asking for a decision."""
                return self._mwp_state.read_back(action_id)

            @function_tool()
            async def execute_action(self, action_id: str) -> str:
                """Run an action the owner has approved after its read-back. Refuses if it is not approved."""
                return self._mwp_state.execute(action_id)

            @function_tool()
            async def cancel_action(self, action_id: str) -> str:
                """Cancel a waiting action so it can never be sent."""
                return self._mwp_state.cancel(action_id)

            @function_tool()
            async def audit_lookup(self, action_id: str) -> str:
                """Look up what the audit trail records for an action, to answer 'why did you do that?'."""
                return self._mwp_state.audit_lookup(action_id)

        return MyWorkPalFreeAgent()


async def _entrypoint(ctx: Any) -> None:
    from livekit import agents
    from livekit.agents import AgentSession

    bridge = _build_bridge()
    assistant_name = os.environ.get("MWP_ASSISTANT_NAME", "Pal")
    business_name = os.environ.get("MWP_BUSINESS_NAME", "Sample Co")
    owner_name = os.environ.get("MWP_OWNER_NAME", "the owner")
    session_kwargs: dict[str, Any] = {}
    # Hosted inference speech (the dial's fast setting) when configured.
    if os.environ.get("MWP_STT_MODEL"):
        session_kwargs["stt"] = os.environ["MWP_STT_MODEL"]
    tts = resolve_tts()
    if tts is not None:
        session_kwargs["tts"] = tts
    try:
        from livekit.plugins import silero

        session_kwargs["vad"] = silero.VAD.load()
    except Exception:
        pass  # VAD is an enhancement; the session still runs without it.
    session = AgentSession(**session_kwargs)
    await ctx.connect()
    if resolve_conversation_mode() == "free":
        state = FreeCallState(bridge, owner_name=owner_name)

        def _on_owner_words(event: Any) -> None:
            # The one road from speech to the gateway in free mode: the
            # owner's own transcribed words, classified by worker code.
            if getattr(event, "is_final", False):
                state.note_owner_utterance(event.transcript)

        session.on("user_input_transcribed", _on_owner_words)
        agent = _FreeAgent.build(
            bridge,
            state,
            assistant_name=assistant_name,
            business_name=business_name,
            owner_name=owner_name,
            llm=resolve_llm_model(),
        )
    else:
        engine = ConversationEngine(
            bridge,
            assistant_name=assistant_name,
            business_name=business_name,
            owner_name=owner_name,
        )
        agent = _EngineAgent.build(bridge, engine)
    await session.start(agent, room=ctx.room)


# ---------------------------------------------------------------------------
# Outbound calling (WP7): the telephony half of the scheduler's decisions.
#
# The scheduler (scheduler.py) decides place / hold / stop and never dials.
# The functions below are the other half: given a decision to place, they
# ring the owner's phone through the LiveKit SIP API and put this worker's
# agent ("my-work-pal") in the room, so the call that answers is the same
# assistant as an inbound call. Nothing here decides *whether* to call;
# the worker dials only when told.
# ---------------------------------------------------------------------------

AGENT_NAME = "my-work-pal"

NOT_PROVISIONED_MESSAGE = (
    "outbound telephony is not provisioned: set MWP_OUTBOUND_TRUNK_ID, "
    "or the carrier SIP settings (MWP_SIP_TRUNK_ADDRESS, MWP_SIP_USERNAME, "
    "MWP_SIP_PASSWORD, MWP_CALLER_ID)"
)


async def ensure_outbound_trunk(lk: Any, config: OutboundConfig) -> str:
    """Return the outbound trunk id, creating the trunk if configured to.

    With ``MWP_OUTBOUND_TRUNK_ID`` set, that trunk is used as-is and no
    API call is made. With the carrier SIP settings, the trunk the worker
    created on an earlier start is reused (matched by name,
    ``OUTBOUND_TRUNK_NAME``); only the first start creates it.
    """
    if config.trunk_id:
        return config.trunk_id
    from livekit import api

    existing = await lk.sip.list_sip_outbound_trunk(
        api.ListSIPOutboundTrunkRequest()
    )
    for trunk in existing.items:
        if trunk.name == OUTBOUND_TRUNK_NAME:
            return trunk.sip_trunk_id
    created = await lk.sip.create_sip_outbound_trunk(
        api.CreateSIPOutboundTrunkRequest(
            trunk=api.SIPOutboundTrunkInfo(
                name=OUTBOUND_TRUNK_NAME,
                address=config.address,
                numbers=[config.caller_id],
                auth_username=config.username,
                auth_password=config.password,
            )
        )
    )
    return created.sip_trunk_id


async def place_outbound_call_async(
    to_number: str,
    *,
    reason: str = "",
    config: OutboundConfig | None = None,
    lk: Any = None,
) -> dict[str, str]:
    """Place one outbound call and put the agent in its room.

    ``to_number`` is the number to ring (E.164). ``reason`` is the
    scheduler's reason for the call (morning brief, urgent, a retry); it
    travels in the dispatch metadata and is never a decision made here.
    Waits until the callee answers, so the caller learns the outcome.
    Returns the room, trunk and participant identifiers for the call log.
    """
    config = resolve_outbound_config() if config is None else config
    if not config.provisioned:
        raise RuntimeError(NOT_PROVISIONED_MESSAGE)
    from livekit import api

    own_client = lk is None
    if own_client:
        lk = api.LiveKitAPI()
    try:
        trunk_id = await ensure_outbound_trunk(lk, config)
        room_name = f"mwp-outbound-{uuid.uuid4().hex[:12]}"
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps(
                    {"direction": "outbound", "reason": reason, "to": to_number}
                ),
            )
        )
        participant = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=to_number,
                sip_number=config.caller_id,
                room_name=room_name,
                participant_identity=f"sip-{to_number}",
                participant_name="Owner (outbound call)",
                wait_until_answered=True,
            )
        )
        return {
            "room": room_name,
            "trunk_id": trunk_id,
            "participant_id": participant.participant_id,
            "sip_call_id": participant.sip_call_id,
            "to": to_number,
        }
    finally:
        if own_client:
            await lk.aclose()


def place_outbound_call(
    to_number: str,
    *,
    reason: str = "",
    config: OutboundConfig | None = None,
    lk: Any = None,
) -> dict[str, str]:
    """Synchronous form of :func:`place_outbound_call_async`.

    For the driver that consumes the scheduler's decisions outside the
    agent session's event loop. Raises the "not provisioned" error,
    unchanged, when no outbound configuration is present.
    """
    config = resolve_outbound_config() if config is None else config
    if not config.provisioned:
        raise RuntimeError(NOT_PROVISIONED_MESSAGE)
    return asyncio.run(
        place_outbound_call_async(to_number, reason=reason, config=config, lk=lk)
    )


def main(argv: list[str] | None = None) -> int:
    try:
        from livekit import agents
    except ImportError:
        print(
            "livekit-agents is not installed in this environment. "
            "Install voice/requirements-voice.txt to run the worker; "
            "the conversation engine and mock call suite run without it.",
            file=sys.stderr,
        )
        return 2
    missing = [
        name
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not os.environ.get(name)
    ]
    if missing and (argv is None or "download-files" not in argv):
        print(
            "Missing LiveKit configuration: " + ", ".join(missing) + ". "
            "No account is provisioned in this stage; see voice/README.md.",
            file=sys.stderr,
        )
        return 2
    outbound = resolve_outbound_config()
    if outbound.partial:
        print(
            "Outbound calling is half-configured: "
            + ", ".join(outbound.partial)
            + " set, but MWP_SIP_TRUNK_ADDRESS, MWP_SIP_USERNAME, "
            "MWP_SIP_PASSWORD and MWP_CALLER_ID are all required together "
            "(or set MWP_OUTBOUND_TRUNK_ID instead).",
            file=sys.stderr,
        )
        return 2
    if outbound.needs_trunk_creation and (argv is None or "download-files" not in argv):
        # Fail loudly at start, not at the first morning call: if the
        # carrier settings are wrong, the deploy should say so now.
        from livekit import api

        async def _ensure() -> str:
            lk = api.LiveKitAPI()
            try:
                return await ensure_outbound_trunk(lk, outbound)
            finally:
                await lk.aclose()

        try:
            trunk_id = asyncio.run(_ensure())
        except Exception as exc:
            print(
                f"Outbound trunk setup failed: {exc}. The worker would "
                "be inbound-only; fix the carrier SIP settings and restart.",
                file=sys.stderr,
            )
            return 2
        print(f"Outbound calling: trunk ready ({OUTBOUND_TRUNK_NAME}, {trunk_id}).")
    elif outbound.trunk_id:
        print("Outbound calling: using the configured MWP_OUTBOUND_TRUNK_ID trunk.")
    elif argv is None or "download-files" not in argv:
        print("Outbound calling: not provisioned (inbound only).")
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=_entrypoint, agent_name=AGENT_NAME)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
