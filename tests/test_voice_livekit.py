"""WP2: the LiveKit worker shell.

Two levels: the worker's refusal behaviour (no livekit, or no account
configuration, is a precise stop, never a stack trace) runs everywhere;
the session test drives the real engine through a real LiveKit
AgentSession using the framework's built-in test run, and skips unless
livekit-agents is installed in the environment running the suite.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared" / "scripts", ROOT / "skills" / "daily-briefing" / "scripts", ROOT / "voice"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mwp_voice import livekit_worker  # noqa: E402


def test_worker_main_refuses_cleanly_without_configuration(monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert livekit_worker.main([]) == 2


def test_outbound_call_is_not_wired_in_this_stage():
    with pytest.raises(RuntimeError, match="not provisioned"):
        livekit_worker.place_outbound_call("+1-555-0100")


@pytest.mark.skipif(
    importlib.util.find_spec("livekit") is None,
    reason="livekit-agents is not installed in this environment",
)
def test_engine_driven_through_a_livekit_session(tmp_path, monkeypatch):
    import asyncio

    from livekit.agents import AgentSession

    from mwp_voice.conversation import ConversationEngine
    from mwp_voice.foundation_bridge import VoiceBridge

    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    bridge = VoiceBridge.sample(tmp_path / "ws")
    action = bridge.seed_follow_up()
    engine = ConversationEngine(
        bridge, assistant_name="Pal", business_name="Sample Co", owner_name="Demo Owner"
    )
    agent = livekit_worker._EngineAgent.build(bridge, engine)

    async def go() -> None:
        async with AgentSession() as session:
            await session.start(agent)
            await session.run(user_input="Yes")   # owner confirms identity
            await session.run(user_input="No")    # declines the queued item

    asyncio.run(go())
    transcript = "\n".join(text for _who, text in engine.transcript)
    assert "Am I speaking with" in transcript       # on_enter opening ran
    assert "morning brief" in transcript            # the engine drove the call
    assert bridge.action_state(action["id"]) == "requested"  # declined, unsent
    assert bridge.sent == []
    bridge.close()
