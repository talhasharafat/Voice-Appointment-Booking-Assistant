# Clinic appointment booking

Django is the source of truth for specialties, doctors, working hours, unavailability, patients, and appointments. Voice booking runs as a **separate LiveKit Agents process** that calls Django only over HTTP.

## App layout

| Piece | Role |
|-------|------|
| `clinic` | Domain models |
| `booking` | Appointment model, calendar, booking services |
| `api` | REST under `/api/` |
| `voice` | `/voice/` UI, LiveKit token, legacy session APIs (tests) |
| `doctors` | Doctor list/detail pages |
| `voice_agent/` | LiveKit Agents worker (Groq STT/LLM + Django tools + Fish TTS) |

## Voice stack

| Layer | Service |
|-------|---------|
| Orchestrator | LiveKit Agents |
| STT | Groq Whisper (`whisper-large-v3-turbo`) |
| LLM | Groq failover: `gpt-oss-20b` → `qwen3.8-27b` → `gpt-oss-120b` |
| Tools | Django `/api/` (specialty, doctors, availability, booking) |
| TTS | Fish Audio (`fish-audio/s2.1-pro-free`, mp3) |

## Setup (Django)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_clinic
python manage.py runserver
```

## Setup (voice agent)

```bash
docker compose -f docker-compose.livekit.yml up -d

python3.11 -m venv .venv-agent
.venv-agent/bin/pip install -r voice_agent/requirements.txt

# Set GROQ_API_KEY and FISH_API_KEY in .env, then:
./scripts/start_voice_agent.sh
```

Open http://127.0.0.1:8000/voice/ , hard-refresh, tap the microphone. Run **one** agent process only.

Swap points: `build_stt` / `build_llm` / `build_tts` in `voice_agent/pipeline.py`.

## API (`/api/`)

| Method | Path |
|--------|------|
| GET | `/api/specialties/` |
| GET | `/api/doctors/?specialty_id=` |
| GET | `/api/availability/?doctor_id=&date=` |
| POST | `/api/appointments/` |

## Environment

| Variable | Purpose |
|----------|---------|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Local LiveKit |
| `DJANGO_API_BASE` | Agent → Django REST API |
| `GROQ_API_KEY` | Groq Whisper STT + LLM |
| `GROQ_LLM_MODELS` | Comma-separated failover models |
| `FISH_API_KEY` / `FISH_TTS_MODEL` / `FISH_RESPONSE_FORMAT` | Fish Audio TTS |
| `VOICE_TURN_SILENCE_SECONDS` | End-of-utterance silence (default `0.75`) |
| `VOICE_MAX_CHAT_ITEMS` | Chat history cap for TPM (default `14`) |

## Tests

```bash
python manage.py test booking.tests.test_reliability api.tests.test_endpoints voice.tests
```
