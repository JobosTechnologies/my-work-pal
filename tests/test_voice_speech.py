"""WP2 speech providers: the mock records honestly; local fails precisely."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "voice",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mwp_voice.speech import (  # noqa: E402
    LocalSpeechProvider,
    MockSpeechProvider,
    SpeechSettings,
    SpeechUnavailable,
)


def test_mock_provider_records_the_transcript():
    provider = MockSpeechProvider()
    provider.speak("Good morning.")
    heard = provider.listen("Yes, go ahead")
    assert heard == "Yes, go ahead"
    assert [(t.speaker, t.text) for t in provider.transcript] == [
        ("assistant", "Good morning."),
        ("owner", "Yes, go ahead"),
    ]


def test_dial_mock_and_local_construct_cloud_is_session_bound():
    assert isinstance(SpeechSettings(mode="mock").make_provider(), MockSpeechProvider)
    assert isinstance(SpeechSettings(mode="local").make_provider(), LocalSpeechProvider)
    with pytest.raises(SpeechUnavailable):
        SpeechSettings(mode="cloud").make_provider()
    with pytest.raises(SpeechUnavailable):
        SpeechSettings(mode="carrier-pigeon").make_provider()


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is not None,
    reason="faster-whisper is installed here; the missing-piece path is moot",
)
def test_local_stt_names_the_missing_piece(tmp_path):
    provider = LocalSpeechProvider()
    with pytest.raises(SpeechUnavailable) as excinfo:
        provider.transcribe(tmp_path / "nope.wav")
    assert "faster-whisper" in str(excinfo.value)


@pytest.mark.skipif(
    importlib.util.find_spec("kokoro") is not None,
    reason="kokoro is installed here; the missing-piece path is moot",
)
def test_local_tts_names_the_missing_piece(tmp_path):
    provider = LocalSpeechProvider()
    with pytest.raises(SpeechUnavailable) as excinfo:
        provider.synthesize("hello", tmp_path / "out.wav")
    assert "kokoro" in str(excinfo.value)
