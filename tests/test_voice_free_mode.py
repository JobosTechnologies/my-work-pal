"""Voice choice + free conversation mode (2026-11-27).

Three levels:

* voice_config resolution is pure: model/voice strings, mode, LLM —
  no livekit needed.
* FreeCallState runs against the sample bridge everywhere: the tools can
  propose and read, execution refuses until the gateway signs, and the
  gateway is reached only through note_owner_utterance classifying the
  owner's actual words (a clear yes, never an unclear one).
* The session test drives a real AgentSession with a stand-in LLM that
  calls the tools: a full free-mode conversation — propose, read-back,
  the owner's yes crossing through worker code, execute, one email in
  the stand-in mailbox. Skips unless livekit-agents is installed.
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
from mwp_voice.foundation_bridge import VoiceBridge  # noqa: E402
from mwp_voice.free_conversation import FreeCallState, build_instructions  # noqa: E402
from mwp_voice.voice_config import (  # noqa: E402
    DEFAULT_LLM_MODEL,
    DEFAULT_TTS_VOICE,
    resolve_conversation_mode,
    resolve_llm_model,
    resolve_outbound_config,
    resolve_tts,
)

# -- voice choice: model-string resolution ---------------------------------


def test_tts_unset_keeps_todays_behaviour():
    assert resolve_tts({}) is None


def test_tts_model_only_gets_the_pilot_default_voice():
    assert resolve_tts({"MWP_TTS_MODEL": "cartesia/sonic-3"}) == (
        f"cartesia/sonic-3:{DEFAULT_TTS_VOICE}"
    )


def test_tts_voice_env_selects_the_voice():
    env = {"MWP_TTS_MODEL": "cartesia/sonic-3", "MWP_TTS_VOICE": "voice-123"}
    assert resolve_tts(env) == "cartesia/sonic-3:voice-123"


def test_tts_voice_alone_implies_the_default_model():
    assert resolve_tts({"MWP_TTS_VOICE": "voice-123"}) == "cartesia/sonic-3:voice-123"


def test_tts_voice_already_in_model_string_is_respected():
    env = {"MWP_TTS_MODEL": "deepgram/aura-2:thalia-en"}
    assert resolve_tts(env) == "deepgram/aura-2:thalia-en"
    env["MWP_TTS_VOICE"] = "pandora-en"
    assert resolve_tts(env) == "deepgram/aura-2:pandora-en"


def test_default_voice_is_the_documented_jacqueline():
    # LiveKit's own sonic-3 example voice (American female); see
    # docs/voice-options.md. Guards against an accidental edit.
    assert DEFAULT_TTS_VOICE == "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"


# -- outbound calling: configuration resolution (WP7) ------------------------

_SIP_ENV = {
    "MWP_SIP_TRUNK_ADDRESS": "sip.signalwire.com",
    "MWP_SIP_USERNAME": "carrier-user",
    "MWP_SIP_PASSWORD": "carrier-pass",
    "MWP_CALLER_ID": "+12402316158",
}


def test_outbound_unset_is_not_provisioned():
    config = resolve_outbound_config({})
    assert not config.provisioned
    assert not config.needs_trunk_creation
    assert config.partial == ()


def test_outbound_existing_trunk_id_provisions_without_creation():
    config = resolve_outbound_config({"MWP_OUTBOUND_TRUNK_ID": "ST_existing"})
    assert config.provisioned
    assert not config.needs_trunk_creation


def test_outbound_full_carrier_set_provisions_with_creation():
    config = resolve_outbound_config(_SIP_ENV)
    assert config.provisioned
    assert config.needs_trunk_creation
    assert config.caller_id == "+12402316158"
    assert config.partial == ()


def test_outbound_partial_carrier_set_is_reported_not_provisioned():
    env = {"MWP_SIP_TRUNK_ADDRESS": "sip.signalwire.com", "MWP_CALLER_ID": "+12402316158"}
    config = resolve_outbound_config(env)
    assert not config.provisioned
    assert set(config.partial) == {"MWP_SIP_TRUNK_ADDRESS", "MWP_CALLER_ID"}


def test_outbound_trunk_id_wins_over_carrier_settings():
    config = resolve_outbound_config({**_SIP_ENV, "MWP_OUTBOUND_TRUNK_ID": "ST_existing"})
    assert config.provisioned
    assert not config.needs_trunk_creation


# -- conversation mode + LLM ------------------------------------------------


def test_conversation_mode_resolution():
    assert resolve_conversation_mode({}) in ("scripted", "free")
    assert resolve_conversation_mode({"MWP_CONVERSATION_MODE": "scripted"}) == "scripted"
    assert resolve_conversation_mode({"MWP_CONVERSATION_MODE": "FREE"}) == "free"
    # A typo falls back to the scripted engine, never to a surprise.
    assert resolve_conversation_mode({"MWP_CONVERSATION_MODE": "banana"}) == "scripted"


def test_llm_model_resolution():
    assert resolve_llm_model({}) == DEFAULT_LLM_MODEL
    assert resolve_llm_model({"MWP_LLM_MODEL": "google/gemini-3.1-flash-lite"}) == (
        "google/gemini-3.1-flash-lite"
    )


def test_instructions_carry_identity_and_conduct_not_steps():
    text = build_instructions(
        assistant_name="WorkPal", business_name="JobOS", owner_name="the owner"
    )
    assert "WorkPal" in text and "JobOS" in text
    assert "no script" in text.lower()
    assert "Only a clear yes counts" in text
    assert "read it back" in text
    assert "share nothing about the business" in text


# -- FreeCallState against the sample bridge --------------------------------


@pytest.fixture()
def call(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    with VoiceBridge.sample(tmp_path / "ws") as bridge:
        yield bridge, FreeCallState(bridge, owner_name="Demo Owner")


def test_propose_queues_and_execute_refuses_until_approved(call):
    bridge, state = call
    reply = state.propose_email(
        "john@acme.example", "Following up: Acme Corp proposal", "Hi John, checking in."
    )
    assert "Proposed, not sent" in reply
    assert "john@acme.example" in reply  # the read-back text is in the reply
    action_id = state.pending_readback_id
    assert action_id is not None
    assert bridge.action_state(action_id) == "requested"
    refused = state.execute(action_id)
    assert "not approved" in refused
    assert bridge.sent == []


def test_clear_yes_through_worker_code_approves_then_execute_sends(call):
    bridge, state = call
    state.propose_email("john@acme.example", "Following up", "Hi John.")
    action_id = state.pending_readback_id
    assert state.note_owner_utterance("Yes, go ahead") == action_id
    assert bridge.action_state(action_id) == "approved"
    done = state.execute(action_id)
    assert done.startswith("Done")
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["to"] == "john@acme.example"
    trail = state.audit_lookup(action_id)
    assert "approved" in trail and "executed" in trail


def test_unclear_words_never_approve(call):
    bridge, state = call
    state.propose_email("john@acme.example", "Following up", "Hi John.")
    action_id = state.pending_readback_id
    for words in ("Hmm, maybe", "I think so", "Whatever you think", ""):
        assert state.note_owner_utterance(words) is None
        assert bridge.action_state(action_id) == "requested"
    assert bridge.sent == []


def test_clear_no_leaves_the_item_waiting_and_clears_the_readback(call):
    bridge, state = call
    state.propose_email("john@acme.example", "Following up", "Hi John.")
    action_id = state.pending_readback_id
    assert state.note_owner_utterance("No, leave it") is None
    assert state.pending_readback_id is None
    assert bridge.action_state(action_id) == "requested"
    # A later stray "yes" with nothing read back approves nothing.
    assert state.note_owner_utterance("Yes") is None
    assert bridge.action_state(action_id) == "requested"


def test_read_back_for_a_seeded_queue_item(call):
    bridge, state = call
    action = bridge.seed_follow_up()
    text = state.read_back(action["id"])
    assert action["payload"]["to"] in text
    assert state.pending_readback_id == action["id"]
    listing = state.review_queue()
    assert action["id"] in listing


def test_cancel_makes_the_item_unsendable(call):
    bridge, state = call
    state.propose_email("john@acme.example", "Following up", "Hi John.")
    action_id = state.pending_readback_id
    assert "cancelled" in state.cancel(action_id)
    assert state.execute(action_id) != ""
    assert bridge.sent == []


# -- the free agent in a real session, with a stand-in LLM -------------------

_HAS_LIVEKIT = importlib.util.find_spec("livekit") is not None


def _fake_llm_class():
    """An LLM that follows a small script keyed off the chat context.

    Turn shape: if the context shows no propose result yet, call the
    propose_email tool; once a propose result exists and the latest user
    message is a yes, call execute_action for the proposed id; otherwise
    answer in plain text. This stands in for the model's judgement — the
    approval itself still crosses only through FreeCallState's hook.
    """
    import json
    import re

    from livekit.agents import llm

    class _Stream(llm.LLMStream):
        def __init__(self, llm_obj, *, chat_ctx, tools, conn_options, chunks):
            super().__init__(
                llm_obj, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options
            )
            self._chunks = chunks

        async def _run(self) -> None:
            for chunk in self._chunks:
                self._event_ch.send_nowait(chunk)

    class FakeLLM(llm.LLM):
        @property
        def label(self) -> str:
            return "mwp-fake-llm"

        @property
        def provider(self) -> str:
            return "my-work-pal"

        @property
        def model(self) -> str:
            return "fake"

        def chat(self, *, chat_ctx, tools=None, conn_options=None, **kwargs):  # noqa: ANN001
            # Agents 1.9 keeps tool calls/results as their own item types:
            # user words are ChatMessages, results are FunctionCallOutput.
            user_texts: list[str] = []
            tool_outputs: list[str] = []
            executed = False
            for item in chat_ctx.items:
                if type(item).__name__ == "FunctionCallOutput":
                    tool_outputs.append(item.output or "")
                elif type(item).__name__ == "FunctionCall":
                    executed = executed or item.name == "execute_action"
                elif getattr(item, "role", None) == "user":
                    if item.text_content:
                        user_texts.append(item.text_content)
            joined = "\n".join(tool_outputs)
            match = re.search(r"Action id (\S+?) is waiting", joined)
            last_user = user_texts[-1] if user_texts else ""
            if match and not executed and last_user.strip().lower().startswith("yes"):
                action_id = match.group(1)
                if last_user.strip().lower().startswith("yes"):
                    chunk = llm.ChatChunk(
                        id="c2",
                        delta=llm.ChoiceDelta(
                            role="assistant",
                            tool_calls=[
                                llm.FunctionToolCall(
                                    name="execute_action",
                                    arguments=json.dumps({"action_id": action_id}),
                                    call_id="call-exec",
                                )
                            ],
                        ),
                    )
                    return _Stream(
                        self, chat_ctx=chat_ctx, tools=tools or [],
                        conn_options=conn_options, chunks=[chunk],
                    )
            if match:
                chunk = llm.ChatChunk(
                    id="c3",
                    delta=llm.ChoiceDelta(
                        role="assistant",
                        content="That is on its way. Anything else?",
                    ),
                )
                return _Stream(
                    self, chat_ctx=chat_ctx, tools=tools or [],
                    conn_options=conn_options, chunks=[chunk],
                )
            chunk = llm.ChatChunk(
                id="c1",
                delta=llm.ChoiceDelta(
                    role="assistant",
                    tool_calls=[
                        llm.FunctionToolCall(
                            name="propose_email",
                            arguments=json.dumps(
                                {
                                    "to": "john@acme.example",
                                    "subject": "Following up: Acme Corp proposal",
                                    "body": "Hi John Tan, checking in on the proposal.",
                                }
                            ),
                            call_id="call-propose",
                        )
                    ],
                ),
            )
            return _Stream(
                self, chat_ctx=chat_ctx, tools=tools or [],
                conn_options=conn_options, chunks=[chunk],
            )

    return FakeLLM()


@pytest.mark.skipif(not _HAS_LIVEKIT, reason="livekit-agents is not installed here")
def test_free_agent_exposes_the_bridge_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    bridge = VoiceBridge.sample(tmp_path / "ws")
    state = FreeCallState(bridge, owner_name="Demo Owner")
    agent = livekit_worker._FreeAgent.build(
        bridge,
        state,
        assistant_name="WorkPal",
        business_name="JobOS",
        owner_name="Demo Owner",
        llm=_fake_llm_class(),
    )
    names = {tool.info.name for tool in agent.tools}
    assert names == {
        "morning_brief",
        "review_queue",
        "propose_email",
        "read_back",
        "execute_action",
        "cancel_action",
        "audit_lookup",
    }
    assert not any("approve" in name or "confirm" in name for name in names)
    bridge.close()


@pytest.mark.skipif(not _HAS_LIVEKIT, reason="livekit-agents is not installed here")
def test_free_mode_conversation_end_to_end(tmp_path, monkeypatch):
    import asyncio

    from livekit.agents import AgentSession

    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(tmp_path / "standin-home"))
    bridge = VoiceBridge.sample(tmp_path / "ws")
    state = FreeCallState(bridge, owner_name="Demo Owner")
    agent = livekit_worker._FreeAgent.build(
        bridge,
        state,
        assistant_name="WorkPal",
        business_name="JobOS",
        owner_name="Demo Owner",
        llm=_fake_llm_class(),
    )

    async def go() -> None:
        async with AgentSession() as session:
            await session.start(agent)
            # Owner asks for an email: the model proposes (nothing sends).
            await session.run(user_input="Please email John about the proposal")
            assert bridge.sent == []
            action_id = state.pending_readback_id
            assert action_id is not None
            assert bridge.action_state(action_id) == "requested"
            # The owner's yes reaches the gateway through worker code —
            # the same hook the worker's transcription listener calls.
            assert state.note_owner_utterance("Yes, go ahead") == action_id
            # The model, told the yes, executes through its tool.
            await session.run(user_input="Yes, go ahead")
            assert len(bridge.sent) == 1
            assert bridge.sent[0]["to"] == "john@acme.example"

    asyncio.run(go())
    bridge.close()
