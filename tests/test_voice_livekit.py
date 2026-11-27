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


_OUTBOUND_ENV_NAMES = (
    "MWP_OUTBOUND_TRUNK_ID",
    "MWP_SIP_TRUNK_ADDRESS",
    "MWP_SIP_USERNAME",
    "MWP_SIP_PASSWORD",
    "MWP_CALLER_ID",
)


def test_outbound_call_without_configuration_is_not_provisioned(monkeypatch):
    for name in _OUTBOUND_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="not provisioned"):
        livekit_worker.place_outbound_call("+1-555-0100")


def test_worker_main_refuses_a_half_configured_outbound(monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setenv(name, "set-for-this-test")
    for name in _OUTBOUND_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MWP_SIP_TRUNK_ADDRESS", "sip.example.com")
    assert livekit_worker.main([]) == 2


class _FakeSip:
    """Records the SIP calls the worker makes; plays the LiveKit API."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.created_trunks = []
        self.participants = []

    async def list_sip_outbound_trunk(self, _request):
        from types import SimpleNamespace

        return SimpleNamespace(items=self.existing)

    async def create_sip_outbound_trunk(self, request):
        from types import SimpleNamespace

        self.created_trunks.append(request.trunk)
        return SimpleNamespace(sip_trunk_id="ST_created")

    async def create_sip_participant(self, request):
        from types import SimpleNamespace

        self.participants.append(request)
        return SimpleNamespace(participant_id="PA_1", sip_call_id="call-1")


class _FakeDispatch:
    def __init__(self):
        self.dispatches = []

    async def create_dispatch(self, request):
        self.dispatches.append(request)


class _FakeLiveKit:
    def __init__(self, existing=()):
        self.sip = _FakeSip(existing)
        self.agent_dispatch = _FakeDispatch()
        self.closed = False

    async def aclose(self):
        self.closed = True


def _sip_config(**overrides):
    from mwp_voice.voice_config import resolve_outbound_config

    env = {
        "MWP_SIP_TRUNK_ADDRESS": "sip.signalwire.com",
        "MWP_SIP_USERNAME": "carrier-user",
        "MWP_SIP_PASSWORD": "carrier-pass",
        "MWP_CALLER_ID": "+12402316158",
    }
    env.update(overrides)
    return resolve_outbound_config(env)


@pytest.mark.skipif(
    importlib.util.find_spec("livekit") is None,
    reason="livekit-agents is not installed in this environment",
)
def test_outbound_call_creates_trunk_dispatches_agent_and_dials():
    import asyncio
    import json

    lk = _FakeLiveKit()
    result = asyncio.run(
        livekit_worker.place_outbound_call_async(
            "+17323189987", reason="scheduled morning brief", config=_sip_config(), lk=lk
        )
    )
    # The trunk was created from the carrier settings, caller ID on it.
    assert len(lk.sip.created_trunks) == 1
    trunk = lk.sip.created_trunks[0]
    assert trunk.name == livekit_worker.OUTBOUND_TRUNK_NAME
    assert trunk.address == "sip.signalwire.com"
    assert trunk.numbers == ["+12402316158"]
    # The agent was dispatched into the call's room, reason attached.
    assert len(lk.agent_dispatch.dispatches) == 1
    dispatch = lk.agent_dispatch.dispatches[0]
    assert dispatch.agent_name == "my-work-pal"
    assert dispatch.room == result["room"]
    assert json.loads(dispatch.metadata)["reason"] == "scheduled morning brief"
    # The participant dials the owner from the caller ID, into that room.
    assert len(lk.sip.participants) == 1
    participant = lk.sip.participants[0]
    assert participant.sip_trunk_id == "ST_created"
    assert participant.sip_call_to == "+17323189987"
    assert participant.sip_number == "+12402316158"
    assert participant.room_name == result["room"]
    assert participant.wait_until_answered
    assert result["participant_id"] == "PA_1"
    assert not lk.closed  # an injected client stays the caller's to close


@pytest.mark.skipif(
    importlib.util.find_spec("livekit") is None,
    reason="livekit-agents is not installed in this environment",
)
def test_outbound_call_reuses_the_trunk_it_created_before():
    import asyncio
    from types import SimpleNamespace

    existing = SimpleNamespace(
        name=livekit_worker.OUTBOUND_TRUNK_NAME, sip_trunk_id="ST_earlier"
    )
    lk = _FakeLiveKit(existing=[existing])
    asyncio.run(
        livekit_worker.place_outbound_call_async(
            "+17323189987", config=_sip_config(), lk=lk
        )
    )
    assert lk.sip.created_trunks == []
    assert lk.sip.participants[0].sip_trunk_id == "ST_earlier"


@pytest.mark.skipif(
    importlib.util.find_spec("livekit") is None,
    reason="livekit-agents is not installed in this environment",
)
def test_outbound_call_with_a_configured_trunk_id_skips_trunk_setup():
    import asyncio

    from mwp_voice.voice_config import resolve_outbound_config

    lk = _FakeLiveKit()
    config = resolve_outbound_config({"MWP_OUTBOUND_TRUNK_ID": "ST_given"})
    asyncio.run(
        livekit_worker.place_outbound_call_async("+17323189987", config=config, lk=lk)
    )
    assert lk.sip.created_trunks == []
    assert lk.sip.participants[0].sip_trunk_id == "ST_given"


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
