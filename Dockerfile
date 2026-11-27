# My Work Pal voice worker (LiveKit agent "my-work-pal") for Railway.
# Foundation deps come from the repo root requirements.txt; the voice
# layer extras (livekit-agents etc.) from voice/requirements-voice.txt.
# Local speech packages (faster-whisper, kokoro) are installed by the
# voice requirements file but unused in the pilot: speech is cloud-first
# via MWP_STT_MODEL / MWP_TTS_MODEL (LiveKit hosted inference).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY voice/requirements-voice.txt ./requirements-voice.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-voice.txt
COPY . .
ENV PYTHONPATH=/app/skills/daily-briefing/scripts:/app/shared/scripts:/app/voice
ENV MWP_STT_MODEL=deepgram/nova-3
ENV MWP_TTS_MODEL=cartesia/sonic-3
CMD ["python", "-m", "mwp_voice.livekit_worker", "start"]
