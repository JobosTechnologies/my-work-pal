#!/usr/bin/env python3
"""Speech providers: the latency dial from PRD Section 13, as an interface.

One interface, three settings, all open source:

* ``mock``  — text in, text out, no audio, no network. This is how the call
  flow is tested end to end (see mock_provider.py), following the
  mock-provider pattern: the full conversation runs before any telephony or
  speech model is involved.
* ``local`` — faster-whisper for listening, Kokoro for speaking, on the
  owner's own machine. Private and free to run; slower on small hardware.
* ``cloud`` — the fast hosted speech used through the LiveKit agent session
  in livekit_worker.py, so call feel can be judged first. The code stays
  open source on either setting; only the speech service differs.

The dial is a setting, not a rewrite: the conversation engine never knows
which provider is attached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SpeechUnavailable(RuntimeError):
    """A speech setting cannot run here; the message says precisely why."""


@dataclass
class TranscriptLine:
    speaker: str  # "assistant" | "owner" | "system"
    text: str
    simulated_latency_ms: int | None = None  # mock only; labelled simulated


class MockSpeechProvider:
    """Text pass-through. Records everything; pretends nothing about audio."""

    name = "mock"
    licence = "part of this package (Apache-2.0)"

    def __init__(self) -> None:
        self.transcript: list[TranscriptLine] = []

    def speak(self, text: str) -> None:
        self.transcript.append(
            TranscriptLine("assistant", text, simulated_latency_ms=0)
        )

    def listen(self, owner_text: str) -> str:
        """In the mock, the scripted owner line IS the transcript."""
        self.transcript.append(
            TranscriptLine("owner", owner_text, simulated_latency_ms=0)
        )
        return owner_text


class LocalSpeechProvider:
    """faster-whisper (STT) + Kokoro (TTS), imported lazily and honestly.

    Construction never fails; the first real use raises SpeechUnavailable
    naming the missing piece, so a machine without the models gets a precise
    answer instead of a stack trace. local_speech_check.py exercises this
    provider end to end.
    """

    name = "local"
    licence = "faster-whisper (MIT) / Kokoro-82M (Apache-2.0)"

    def __init__(self, whisper_model: str = "tiny", language: str = "en") -> None:
        self.whisper_model = whisper_model
        self.language = language
        self._stt: Any = None
        self._tts: Any = None

    def _load_stt(self) -> Any:
        if self._stt is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechUnavailable(
                    "faster-whisper is not installed in this environment "
                    f"({exc}); install voice/requirements-voice.txt to use "
                    "the local speech setting"
                ) from exc
            self._stt = WhisperModel(
                self.whisper_model, device="cpu", compute_type="int8"
            )
        return self._stt

    def _load_tts(self) -> Any:
        if self._tts is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise SpeechUnavailable(
                    "kokoro is not installed in this environment "
                    f"({exc}); install voice/requirements-voice.txt to use "
                    "the local speech setting"
                ) from exc
            self._tts = KPipeline(lang_code="a")
        return self._tts

    def transcribe(self, wav_path: Path | str) -> str:
        model = self._load_stt()
        segments, _info = model.transcribe(str(wav_path), language=self.language)
        return " ".join(seg.text.strip() for seg in segments).strip()

    def synthesize(self, text: str, out_path: Path | str) -> Path:
        pipeline = self._load_tts()
        import soundfile as sf

        chunks = []
        for _gs, _ps, audio in pipeline(text, voice="af_heart"):
            chunks.append(audio)
        if not chunks:
            raise SpeechUnavailable("Kokoro produced no audio for the text")
        import numpy as np

        sf.write(str(out_path), np.concatenate(chunks), 24000)
        return Path(out_path)


@dataclass
class SpeechSettings:
    """The dial. ``mode`` is one of: mock, local, cloud."""

    mode: str = "mock"
    whisper_model: str = "tiny"

    def make_provider(self) -> Any:
        if self.mode == "mock":
            return MockSpeechProvider()
        if self.mode == "local":
            return LocalSpeechProvider(whisper_model=self.whisper_model)
        if self.mode == "cloud":
            raise SpeechUnavailable(
                "the cloud setting runs inside the LiveKit agent session "
                "(see livekit_worker.py); it is not a standalone provider"
            )
        raise SpeechUnavailable(f"unknown speech mode: {self.mode!r}")
