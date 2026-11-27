#!/usr/bin/env python3
"""Exercise the local speech setting once: Kokoro speaks, faster-whisper listens.

The plan's latency dial (WP2/WP6) requires the local option to be *proven*,
not just drawn: this script synthesises one line with Kokoro, transcribes
the result with faster-whisper (tiny model, CPU), checks the words survived
the round trip, and prints the timings — the numbers that belong beside the
cloud setting's when call feel is judged.

Run it in an environment with the voice extras installed:

    python -m mwp_voice.local_speech_check     (with voice/ on PYTHONPATH)

Exit codes: 0 = round trip passed; 3 = a component is unavailable here
(the printed reason says precisely what is missing); 4 = the round trip
ran but the words did not survive.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

_VOICE_DIR = Path(__file__).resolve().parent
if str(_VOICE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_VOICE_DIR.parent))

from mwp_voice.speech import LocalSpeechProvider, SpeechUnavailable  # noqa: E402

LINE = "This is My Work Pal. Your morning brief is ready."
EXPECTED_WORDS = {"morning", "brief"}


def run(out: Callable[[str], None] = print) -> int:
    provider = LocalSpeechProvider(whisper_model="tiny")
    tmp = Path(tempfile.mkdtemp(prefix="mwp-speech-check-"))
    wav = tmp / "line.wav"
    try:
        t0 = time.perf_counter()
        provider.synthesize(LINE, wav)
        tts_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        heard = provider.transcribe(wav)
        stt_s = time.perf_counter() - t0
    except SpeechUnavailable as exc:
        out(f"LOCAL SPEECH UNAVAILABLE: {exc}")
        return 3
    except Exception as exc:  # model download, runtime, audio errors
        out(f"LOCAL SPEECH FAILED: {type(exc).__name__}: {exc}")
        return 3
    out(f"Spoke:   {LINE}")
    out(f"Heard:   {heard}")
    out(f"Kokoro synthesis: {tts_s:.2f}s; faster-whisper transcription: {stt_s:.2f}s")
    words = {w.strip(".,!?").lower() for w in heard.split()}
    if EXPECTED_WORDS <= words:
        out("LOCAL SPEECH ROUND TRIP: PASS")
        return 0
    out(f"LOCAL SPEECH ROUND TRIP: FAIL (expected words {sorted(EXPECTED_WORDS)} in transcript)")
    return 4


if __name__ == "__main__":
    raise SystemExit(run())
