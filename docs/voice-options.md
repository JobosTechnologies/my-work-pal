# Voice options — how My Work Pal picks a speaking voice

Researched 2026-11-27, after the owner rejected the model-default voice
(a heavy male) on the first live calls. He asked for: smooth, natural,
beautiful intonation and cadence, and a real choice — British, Indian,
African/Nigerian, American accents, male and female, open source
included.

## How selection works (verified in LiveKit's docs)

LiveKit Inference selects a TTS voice inside the model string passed to
the agent session:

```
tts = "cartesia/sonic-3:{voice_id}"
```

The same `model:voice` shape works for `deepgram/aura-2:{voice}` and
`elevenlabs/eleven_turbo_v2_5:{voice_id}`. No provider API key is needed
on this path; LiveKit Cloud bills the inference. In code this is
`MWP_TTS_MODEL` + `MWP_TTS_VOICE`, resolved by
`voice/mwp_voice/voice_config.py`. With only a model set, the pilot
default voice below is appended.

**Pilot default (live now): Jacqueline** —
`cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc`
American female, described by LiveKit as a confident young adult female;
it is the example voice in LiveKit's own Sonic 3 documentation, chosen
because it is warm and natural rather than heavy. First alternates to
audition: Amanda and Allie (below).

**How an owner switches later:** the voice is a per-owner setting, held
with the owner's other assistant settings (like the assistant's name).
At worker start the owner's setting becomes `MWP_TTS_VOICE` (and
`MWP_TTS_MODEL` if they leave Cartesia). Today, for the pilot, it is the
deployment's environment variables; changing them redeploys the worker
and the next call uses the new voice. Nothing about the voice is baked
into the conversation code.

## Cartesia Sonic 3 — via LiveKit Inference (works today, no new account)

LiveKit Inference supports all default Cartesia library voices. IDs
below are Cartesia's own library IDs, cross-checked in Cartesia's
catalogue as mirrored by SLNG and SignalWire.

| Voice | ID | Accent | Gender | Notes |
|---|---|---|---|---|
| Jacqueline | `9626c31c-bec5-4cca-baa8-f8ba9e84c8bc` | American | Female | **Pilot default.** Confident, natural. |
| Amanda — Warm Guide | `b60048c2-abb5-43fa-b403-90dce232e55e` | American | Female | Inviting, approachable; the warmest description in the catalogue. |
| Allie — Natural Conversationalist | `2747b6cf-fa34-460c-97db-267566918881` | American | Female | Built for natural conversational support. |
| British Lady | `79a125e8-cd45-4c13-8a67-188112f4dd22` | British | Female | |
| Charlotte — Heiress | `71a7ad14-091c-4e8e-a314-022ece01c121` | British | Female | Elegant, young adult. |
| Confident British Man | `63ff761f-c1e8-414b-b969-d1833d1c870c` | British | Male | |
| Alistair — Composed Consultant | `c8f7835e-28a3-4f0c-80d7-c1302ac62aae` | British | Male | Sophisticated, steady. |
| Indian Lady | `3b554273-4299-48b9-9aaf-eefd438e3941` | Indian | Female | |
| Aarti — Conversationalist | `9cebb910-d4b7-4a4a-85a4-12c79137724c` | Indian | Female | Relatable dialogue voice. |
| Indian Man | `638efaaa-4d0c-442e-b701-3fae16aad012` | Indian | Male | |
| Krishna — Friendly Pal | `c63361f8-d142-4c62-8da7-8f8149d973d6` | Indian | Male | Easygoing, slight Indian accent. |
| Anele — Bright Presenter | `072d954b-8379-4b6b-816a-bb0cd38725f8` | South African | Female | Clear, for customer service. |
| Pieter — Polished Analyst | `baf84392-fa95-4d44-8871-d32ee36b0e01` | South African | Male | Clear, articulate. |

**Gap:** Cartesia's English library has **no Nigerian-accented voice**
(the full catalogue was searched). Its localize feature can re-accent a
voice (American, British, Australian, Indian, Southern), but custom and
localized voices are *not* available through LiveKit Inference — they
need a Cartesia account and the Cartesia plugin.

## ElevenLabs — where the Nigerian voices are (paid)

Two routes: the LiveKit Inference string
`elevenlabs/eleven_turbo_v2_5:{voice_id}` (billed by LiveKit), or the
LiveKit ElevenLabs plugin with our own `ELEVENLABS_API_KEY` (billed by
ElevenLabs, per character; Turbo/Flash models cost fewer credits per
character). Library voices generally must be added to an ElevenLabs
account before use, which points to the plugin route for these:

| Voice | ID | Accent | Gender | Notes |
|---|---|---|---|---|
| Jemmyeh | `AWxJjhmijML4C3mGaEAT` | Nigerian | Female | Rich Nigerian accent; storytelling. |
| Hoyeen | `V2D1qkaFj5NormT9yoaK` | Nigerian | Female | Clear, smooth, steady, soothing. |
| Joe | `LEvd0YiWkwZ6hTZOmdVE` | Nigerian | Male | Warm, confident; made for conversational AI. |
| Emmanuel | `tj0Lij6AHHeCO5FylYzu` | Nigerian | Male | Subtle African/Nigerian accent, neutral. |
| Rachel (premade) | `21m00Tcm4TlvDq8ikWAM` | American | Female | ElevenLabs' standard female. |
| George (premade) | `JBFqnCBsd6RMkjVDRZzb` | British | Male | Warm British narrator. |

Cost shape: a paid ElevenLabs plan (subscription with a character
quota, overage per character) on top of, or instead of, LiveKit
inference billing. This is the one option that needs the owner's
decision and a new account/key.

## Deepgram Aura-2 — via LiveKit Inference (cheap, fewer accents)

String form: `deepgram/aura-2:{voice}`, e.g. `deepgram/aura-2:thalia-en`.

| Voice | Accent | Gender | Notes |
|---|---|---|---|
| `thalia-en` | American | Female | Clear, confident; Deepgram's safe default. |
| `pandora-en` | British | Female | Smooth, calm. |
| `draco-en` | British | Male | Warm, approachable. |
| `odysseus-en` | American | Male | Calm, smooth. |

Aura-2's English set covers American, British, Australian, Filipino and
Southern accents — **no Indian or African English voices**. Direct
Deepgram pricing is per character and among the cheapest hosted
options; through LiveKit it is inference billing as above.

## Open source (self-hosted; the privacy end of the dial)

| Model | License | Voices / accents | Runs where |
|---|---|---|---|
| Kokoro-82M | Apache-2.0 | 54 voices: American (`af_heart` warm F, `af_nicole` soft F, `am_michael` M) and British (`bf_emma`, `bf_isabella` F; `bm_george`, `bm_fable` M); Hindi voices are Hindi-language, not Indian-English accent; no African English voices | Already wired as the `local` speech setting. Measured RTF 1.24 on this project's small VM CPU — slower than real time, so **not** viable in the Railway container for live calls; fine on the owner's machine or a larger host. |
| Chatterbox (Resemble AI) | MIT (output carries an inaudible watermark) | Zero-shot cloning from a short reference clip — any accent, including Nigerian, if we have a clip we may use | GPU recommended (real time on a mid-range NVIDIA); CPU too slow for live calls. Retired/unmaintained since Aug 2026 — usable, but no fixes coming. |
| XTTS-v2 (Coqui) | **Coqui Public Model License — non-commercial only, and the outputs inherit it** | Clones any accent from ~6 s of audio, 17 languages | GPU real-time; CPU well below. **Dead end for My Work Pal:** Coqui shut down in Jan 2024, so no commercial license can be bought. Listed so the option is closed on the record, not rediscovered later. |

## Sources

- LiveKit, Cartesia TTS (model strings, `model:voice` form, inference
  voice support): https://docs.livekit.io/agents/models/tts/cartesia/
- LiveKit, LLM models (Gemma 4 31B recommendation):
  https://docs.livekit.io/agents/models/llm/
- Cartesia Sonic 3 voice catalogue (IDs, accents, genders):
  https://docs.slng.ai/models/voices/cartesia-sonic-3 and
  https://signalwire.com/docs/platform/voice/tts/cartesia
- ElevenLabs Nigerian voices (IDs): the ElevenLabs voice library as
  catalogued at https://json2video.com/ai-voices/elevenlabs/voices/AWxJjhmijML4C3mGaEAT/
- Deepgram Aura-2 voice accents:
  https://docs.televox.ai/reference/voices-and-models
- Kokoro-82M voice pack (54 voices, Apache-2.0):
  https://huggingface.co/litert-community/Kokoro-82M/blob/main/README.md
- Open-source TTS licenses compared (XTTS CPML, Chatterbox MIT):
  https://www.cartesia.ai/learn/open-source-tts
