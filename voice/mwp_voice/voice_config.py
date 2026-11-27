#!/usr/bin/env python3
"""Voice-layer configuration: pure functions over the environment.

Everything here is a plain setting an owner (or a deployment) can change
without touching code, resolved in one place so the worker, the tests, and
the docs all agree:

* ``MWP_TTS_MODEL`` / ``MWP_TTS_VOICE`` — which hosted-inference voice
  speaks. LiveKit Inference selects a TTS voice inside the model string:
  ``"cartesia/sonic-3:{voice_id}"`` (the same ``model:voice`` shape works
  for ``deepgram/aura-2:{voice}`` and
  ``elevenlabs/eleven_turbo_v2_5:{voice_id}``). See docs/voice-options.md
  for the shortlist and how an owner switches.
* ``MWP_CONVERSATION_MODE`` — ``scripted`` (the WP2 conversation engine
  walks the call) or ``free`` (a real LLM converses, with the foundation
  bridge as tools; see free_conversation.py). Unknown values fall back to
  ``scripted``: a typo must never silently change how a live call behaves.
* ``MWP_LLM_MODEL`` — the LiveKit Inference LLM string used in free mode.
* Outbound calling (WP7) — ``MWP_OUTBOUND_TRUNK_ID`` (a SIP outbound
  trunk that already exists in the LiveKit project), or the carrier SIP
  settings ``MWP_SIP_TRUNK_ADDRESS`` / ``MWP_SIP_USERNAME`` /
  ``MWP_SIP_PASSWORD`` / ``MWP_CALLER_ID``, from which the worker creates
  the trunk at start. Neither set means outbound is not provisioned and
  dialling raises, as it did before WP7.

The defaults are the pilot's choices, recorded here once: the TTS voice is
Cartesia's Jacqueline (American female, warm and natural — the voice
LiveKit's own docs use as the sonic-3 example), and the LLM is Gemma 4
31B, the model LiveKit recommends as the default for voice agents.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# Cartesia Sonic 3 library voice "Jacqueline" — confident, young American
# adult female (LiveKit docs, Cartesia TTS page). Chosen 2026-11-27 after
# the owner rejected the model-default voice as a heavy male.
DEFAULT_TTS_VOICE = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
DEFAULT_TTS_MODEL = "cartesia/sonic-3"

# LiveKit Inference's recommended default LLM for voice agents
# (docs.livekit.io/agents/models/llm: "Gemma 4 31B is the recommended
# default LLM... latency-optimized, hosted by LiveKit").
DEFAULT_LLM_MODEL = "google/gemma-4-31b-it"

# The pilot default conversation mode. Free mode shipped as the default
# once its mock end-to-end run and the full suite passed (2026-11-27);
# MWP_CONVERSATION_MODE=scripted restores the WP2 engine at any time.
DEFAULT_CONVERSATION_MODE = "free"

_MODES = ("scripted", "free")


def resolve_tts(env: Mapping[str, str] | None = None) -> str | None:
    """The ``tts`` string for the AgentSession, or None for today's default.

    Rules, in order:

    * Neither variable set: ``None`` — the session is built exactly as
      before this setting existed (no ``tts`` argument at all).
    * ``MWP_TTS_VOICE`` set: that voice is used. With no model set, the
      model is :data:`DEFAULT_TTS_MODEL`.
    * Only ``MWP_TTS_MODEL`` set: the pilot default voice is appended,
      unless the model string already carries a voice (``model:voice``),
      which is respected as-is.
    """
    env = os.environ if env is None else env
    model = (env.get("MWP_TTS_MODEL") or "").strip()
    voice = (env.get("MWP_TTS_VOICE") or "").strip()
    if not model and not voice:
        return None
    if not model:
        model = DEFAULT_TTS_MODEL
    if voice:
        base = model.split(":", 1)[0]
        return f"{base}:{voice}"
    if ":" in model:
        return model
    return f"{model}:{DEFAULT_TTS_VOICE}"


def resolve_conversation_mode(env: Mapping[str, str] | None = None) -> str:
    """``scripted`` or ``free``; anything unrecognised is ``scripted``."""
    env = os.environ if env is None else env
    mode = (env.get("MWP_CONVERSATION_MODE") or "").strip().lower()
    if not mode:
        return DEFAULT_CONVERSATION_MODE
    return mode if mode in _MODES else "scripted"


def resolve_llm_model(env: Mapping[str, str] | None = None) -> str:
    """The LiveKit Inference LLM model string for free mode."""
    env = os.environ if env is None else env
    return (env.get("MWP_LLM_MODEL") or "").strip() or DEFAULT_LLM_MODEL


# The name the worker gives the outbound trunk it creates in the LiveKit
# project, so a restart reuses it instead of piling up duplicates.
OUTBOUND_TRUNK_NAME = "mwp-outbound"

# The carrier settings, all four required together: a trunk address with
# no credentials, or credentials with no caller ID, cannot place a call.
_SIP_ENV_NAMES = (
    "MWP_SIP_TRUNK_ADDRESS",
    "MWP_SIP_USERNAME",
    "MWP_SIP_PASSWORD",
    "MWP_CALLER_ID",
)


@dataclass(frozen=True)
class OutboundConfig:
    """Outbound telephony settings, resolved from the environment.

    Provisioned two ways, in order: an existing trunk id
    (``MWP_OUTBOUND_TRUNK_ID``), or the full carrier SIP set, from which
    the worker creates the trunk at start. ``partial`` names carrier
    settings that were supplied but incomplete, so a half-filled
    configuration is reported as broken rather than read as absent.
    """

    trunk_id: str = ""
    address: str = ""
    username: str = ""
    password: str = ""
    caller_id: str = ""
    partial: tuple[str, ...] = ()

    @property
    def provisioned(self) -> bool:
        if self.trunk_id:
            return True
        return bool(self.address and self.username and self.password and self.caller_id)

    @property
    def needs_trunk_creation(self) -> bool:
        return self.provisioned and not self.trunk_id


def resolve_outbound_config(env: Mapping[str, str] | None = None) -> OutboundConfig:
    """Resolve the outbound settings; empty strings count as unset."""
    env = os.environ if env is None else env

    def _get(name: str) -> str:
        return (env.get(name) or "").strip()

    sip = {name: _get(name) for name in _SIP_ENV_NAMES}
    present = tuple(name for name in _SIP_ENV_NAMES if sip[name])
    partial = present if 0 < len(present) < len(_SIP_ENV_NAMES) else ()
    return OutboundConfig(
        trunk_id=_get("MWP_OUTBOUND_TRUNK_ID"),
        address=sip["MWP_SIP_TRUNK_ADDRESS"],
        username=sip["MWP_SIP_USERNAME"],
        password=sip["MWP_SIP_PASSWORD"],
        caller_id=sip["MWP_CALLER_ID"],
        partial=partial,
    )
