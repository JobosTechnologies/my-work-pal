# My Work Pal — voice layer (WP2, first stage)

The phone call, on top of the Chief of Staff foundation. An owner calls (or
is called), hears the morning brief, walks the Review Queue by voice, hears
a read-back of recipient and subject, and approves out loud — and nothing
sends, ever, without that read-back and a clear yes, signed through the
foundation's own approval path.

This stage is the first ring plus the machinery around it, proven locally
with a mock provider. No phone number, SIP trunk, or paid service is
provisioned yet; see "What remains for real telephony" below.

## Layout

| File | What it is |
|---|---|
| `mwp_voice/foundation_bridge.py` | The only door to the foundation: brief, queue previews, audit, cancellation, guarded execution. Holds the `ApprovalGateway`, the one place a spoken decision becomes a *signed* approval. |
| `mwp_voice/conversation.py` | The conversation engine: turn-taking state, intent, the repair set ("say that again", "the other one", "stop the task", "why did you do that?"), and the ask-again ladder when it does not understand. It can ask for a decision; it cannot approve. |
| `mwp_voice/speech.py` | The latency dial as an interface: `mock`, `local` (faster-whisper + Kokoro), `cloud` (inside the LiveKit session). |
| `mwp_voice/mock_provider.py` | A full call with no phone: scripted owner lines drive the real engine, gateway, and execution path; the record is transcript + events + audit. |
| `mwp_voice/scheduler.py` | Outbound logic on the foundation's cron store: owner-set morning call time, quiet hours (held and logged, wake-up exception), the 10/30-minute retry pattern, the urgent-call daily limit, bad-time notes. Every decision is logged. It decides; it does not dial. |
| `mwp_voice/livekit_worker.py` | The LiveKit Agents worker (written against livekit-agents 1.9): session, barge-in, and speech around the same engine the mock suite proves. |
| `mwp_voice/demo_call.py` | Two scripted calls, printed with checks: the full loop, and stop-the-task. |
| `mwp_voice/local_speech_check.py` | Exercises the local speech setting once: Kokoro speaks a line, faster-whisper transcribes it back, timings printed. |

## The approval separation (why the engine cannot approve)

The foundation's rule is that the agent's own tools are read-only and only
the human approves, with a signed decision. The voice layer keeps that
shape exactly:

1. The engine hears a clear yes and emits a `decision_made` event. It holds
   no signing key and has no method that approves.
2. The call driver (the worker's human-channel side; the mock driver in
   tests) hands the decision to `ApprovalGateway.confirm`, which signs only
   a clear yes, only for an action still waiting in `requested`.
3. Execution runs only on the gateway-approved action, through the
   foundation's guarded path (claim → account check → capability check →
   single-use grant → record). A second yes finds the work done and
   creates nothing; "stop the task" kills a pending item so it can never
   run later; an unclear reply is asked again, then held open — never
   guessed into an approval.

## Run the proof

From the repo root, with the foundation venv:

```sh
PYTHONPATH=voice .venv/bin/python -m mwp_voice.demo_call
PYTHONPATH=voice .venv/bin/python -m pytest tests/test_voice_bridge.py \
    tests/test_voice_conversation.py tests/test_voice_scheduler.py \
    tests/test_voice_speech.py tests/test_voice_livekit.py
```

`tests/test_voice_livekit.py` additionally drives the engine through a real
LiveKit `AgentSession` using the framework's built-in test run
(`session.run`); it skips unless livekit-agents is installed.

Local speech check, in an environment with the voice extras:

```sh
PYTHONPATH=voice python -m mwp_voice.local_speech_check
```

### Local speech exercise — WP2 result (Nov 26, 2026)

Exercised once, as the plan requires, on this machine (small shared CPU):

* **faster-whisper** (tiny model, CPU, int8): installed and ran; a 2.84s
  clip transcribed in **1.12s**, words intact.
* **Kokoro**: the official `kokoro` package pulls in torch plus its CUDA
  library tree (multiple GB); in this environment the install ran 25
  minutes without a single package landing and was stopped. The exercise
  was completed with the same Kokoro-82M model through its ONNX runtime
  port (`kokoro-onnx`, Apache-2.0): synthesis of the check line took
  **3.52s for 2.84s of audio (RTF 1.24)** — slower than real time on this
  small VM, a hardware note for the dial, not a model verdict. Round trip:
  spoke "This is My Work Pal. Your morning brief is ready.", heard back
  "This is my workpal, your morning brief is ready." — **PASS**.
* One environment trap found and pinned in `requirements-voice.txt`:
  faster-whisper 1.2 passes `metadata_errors` to PyAV's `av.open()`, which
  PyAV 15+ removed; unpinned installs resolve to that broken pairing.

## Licences

| Component | Licence |
|---|---|
| This package, the foundation | Apache-2.0 |
| LiveKit Agents / LiveKit Server | Apache-2.0 |
| faster-whisper | MIT |
| Kokoro (model and `kokoro` package) | Apache-2.0 |
| Silero VAD (via `livekit-plugins-silero`) | MIT |

**Footnote — LiveKit turn-detection models:** the LiveKit Agents core is
Apache-2.0, but LiveKit's pre-trained turn-detection models (the
multilingual turn detector plugin) are distributed under the separate
**LiveKit Model License**, not Apache-2.0. The worker treats the turn
detector as an optional attachment for exactly this reason: the session
runs without it, and adopting the model is a licensing decision to record
before the pilot, not a default smuggled in by a dependency.

## What remains for real telephony

* A LiveKit account (or a self-hosted LiveKit Server) and its URL/API
  credentials in managed secrets — needed from Ibrahim.
* A phone number and SIP trunk provisioned in that account (the plan
  stages the pilot on LiveKit's free Build tier: first US number free,
  1,000 agent minutes as the test budget).
* Wiring `scheduler.py` decisions to LiveKit SIP outbound
  (`place_outbound_call` in the worker raises until this exists), and the
  inbound dispatch rule that routes the number to this worker.
* Owner verification on real calls (known owner number plus the spoken
  check) switched from the pilot profile into the worker's opening.
* The WP6 acceptance runs: six spoken scenarios, three consecutive passes
  each, on real calls — including exercising the local speech setting once
  on Scenario 2 with its latency recorded beside the cloud setting's.
