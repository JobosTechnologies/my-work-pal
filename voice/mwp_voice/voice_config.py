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

The defaults are the pilot's choices, recorded here once: the TTS voice is
Cartesia's Jacqueline (American female, warm and natural — the voice
LiveKit's own docs use as the sonic-3 example), and the LLM is Gemma 4
31B, the model LiveKit recommends as the default for voice agents.
"""
from __future__ import annotations

import os
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
