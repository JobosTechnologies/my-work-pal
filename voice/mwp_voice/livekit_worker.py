#!/usr/bin/env python3
"""The LiveKit voice worker: the same engine, on a real call.

This is a thin transport shell around conversation.ConversationEngine,
written against LiveKit Agents 1.9 (Apache-2.0), the version pinned in
voice/requirements-voice.txt:

* LiveKit owns the audio: the session, turn-taking and interruption
  (barge-in) handling, and speech in and out. When the owner interrupts,
  the session stops the current speech itself; the engine's ``interrupt``
  hook is called so queued lines are dropped too.
* The engine owns the conversation: brief, queue, read-back, repairs.
* The gateway owns approval. This module holds the bridge's gateway (the
  human-channel side of the worker) and passes a spoken clear yes to it,
  exactly as the mock driver does. The engine never sees it.
* Speech is the latency dial: by default the session uses LiveKit's hosted
  inference speech models (fast, for judging call feel); the local setting
  (faster-whisper + Kokoro) attaches behind the same session shape in a
  later step. The turn-detection model is LiveKit's own and sits under the
  separate LiveKit Model License, not Apache-2.0 — see voice/README.md.

What this stage does NOT do: no phone number, SIP trunk, or LiveKit
account is provisioned, and outbound dialling raises until one is (the
scheduler's decisions are proven separately; placing the call is the
telephony step). Running this file without livekit-agents installed, or
without LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the
environment, stops with a precise message instead of a stack trace.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .conversation import ConversationEngine
from .foundation_bridge import VoiceBridge


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


async def _entrypoint(ctx: Any) -> None:
    from livekit import agents
    from livekit.agents import AgentSession

    bridge = _build_bridge()
    engine = ConversationEngine(
        bridge,
        assistant_name=os.environ.get("MWP_ASSISTANT_NAME", "Pal"),
        business_name=os.environ.get("MWP_BUSINESS_NAME", "Sample Co"),
        owner_name=os.environ.get("MWP_OWNER_NAME", "the owner"),
    )
    session_kwargs: dict[str, Any] = {}
    # Hosted inference speech (the dial's fast setting) when configured.
    if os.environ.get("MWP_STT_MODEL"):
        session_kwargs["stt"] = os.environ["MWP_STT_MODEL"]
    if os.environ.get("MWP_TTS_MODEL"):
        session_kwargs["tts"] = os.environ["MWP_TTS_MODEL"]
    try:
        from livekit.plugins import silero

        session_kwargs["vad"] = silero.VAD.load()
    except Exception:
        pass  # VAD is an enhancement; the session still runs without it.
    session = AgentSession(**session_kwargs)
    await ctx.connect()
    await session.start(_EngineAgent.build(bridge, engine), room=ctx.room)


def place_outbound_call(*_args: Any, **_kwargs: Any) -> None:
    """Dialling out is deliberately not wired in this stage.

    The scheduler (scheduler.py) already decides when a call may be placed;
    turning a decision into a ringing phone needs a provisioned number and
    SIP trunk (LiveKit SIP CreateSIPParticipant), which is a later WP2 step
    and needs Ibrahim's LiveKit account. Raising here keeps that boundary
    impossible to cross by accident.
    """
    raise RuntimeError(
        "outbound telephony is not provisioned in this stage: no number, "
        "no SIP trunk, no LiveKit account credentials"
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
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=_entrypoint, agent_name="my-work-pal")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
