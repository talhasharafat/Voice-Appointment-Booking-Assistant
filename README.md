# Voice Appointment Booking Assistant

Django prototype of a **browser voice scheduling desk** for a clinic. Patients speak (or type) in the browser; the backend matches a specialty, checks real doctor availability, reads the plan back, and only then creates a confirmed appointment with a booking reference.

This build uses **free tools only**:

- Browser **Web Speech API** for speech-to-text
- Browser **`speechSynthesis`** for text-to-speech
- Django **rule-based conversation** (no paid LLM API)
- Django **booking services** as the source of truth for slots and bookings

It does **not** diagnose conditions or prescribe treatment. Use synthetic patient data for demos.

---

## Quick start (reproducible local setup)

Requirements: Python 3.9+, a modern browser (**Chrome recommended** for Web Speech).

```bash
cd /Users/apple/Downloads/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_clinic
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver
```

Open:

- Voice desk: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- Staff admin: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

Allow the microphone when prompted. Tap **Start booking** or the mic, then answer the assistant. You can also type replies if speech recognition is unavailable.

### Run automated checks

```bash
python manage.py test
```

Coverage includes calendar rules, booking conflicts, idempotent retries, concurrent same-slot bookings, voice tools, conversation flow, and failure handling.

---

## Environment variables

Copy `.env.example` to `.env`. Secrets stay out of git (`.env` is gitignored).

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | required in production | Django signing secret (cookies/sessions/CSRF). `DJANGO_SECRET_KEY` still accepted as an alias. |
| `DJANGO_DEBUG` | `true` | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Allowed hosts |
| `CLINIC_TIMEZONE` | `Asia/Karachi` | **One** clinic timezone for all slots |
| `APPOINTMENT_DURATION_MINUTES` | `30` | Fixed appointment length |
| `AI_PROVIDER` | `local` | Free local adapter (`fake` for tests) |
| `SPEECH_BACKEND` | `browser` | Client Web Speech only |
| `VOICE_SESSION_TIMEOUT_MINUTES` | `30` | Idle chats auto-close with no booking |
| `DJANGO_DB_ENGINE` / `DJANGO_DB_NAME` | SQLite file | Optional DB override |

No paid API keys are required or used.

---

## Architecture

Layers stay separate so voice transport, dialogue, and scheduling can evolve independently.

```
Browser (Web Speech STT/TTS)
        │  text turns
        ▼
voice app  ── conversation state machine
        │         │
        │         ▼
        │    voice.tools (structured tools)
        │         │
        ▼         ▼
   VoiceSession   booking.services + booking.calendar
   (draft only)          │
                         ▼
              clinic models + Appointment (confirmed only)
```

| App | Responsibility |
|---|---|
| `clinic` | Specialties, doctors, working hours, unavailability, patients |
| `booking` | Fixed-length calendar, availability, conflict/idempotent create APIs |
| `voice` | Browser UI, session store, parsers, rule-based dialogue, tools |
| `ai` | Replaceable provider adapters (`local`, `fake`) — dialogue does not depend on a paid model |
| `config` | Settings, URLs |

### Why the backend owns booking

The assessment requires that the model (or dialogue layer) is **not** the sole authority for whether a slot can be booked. Every create path goes through `booking.services.create_appointment`, which:

1. Re-checks `is_slot_bookable`
2. Locks the doctor row (`select_for_update`)
3. Enforces unique confirmed `(doctor, starts_at)` and unique `idempotency_key`
4. Returns a booking reference only after the row is saved

Announcing success without that reference is treated as a failure in the voice layer and UI (`booked: false`).

### Patient journey (implemented)

1. Collect name, phone, reason, preferred date/time  
2. Match specialty from registered descriptions; clarify when ambiguous  
3. Find doctors and check real availability  
4. Offer alternatives if the preferred slot is free elsewhere / taken  
5. Read back details and require explicit **yes**  
6. Save appointment and speak the booking reference  

Abandoned or timed-out `VoiceSession` rows never create an `Appointment`.

### Telephone extension (designed, not built)

Adding phone later needs a **telephony/media adapter** and call lifecycle handling (ring, answer, media stream, hangup), not only a new frontend. Keep:

- `booking.services` unchanged as the scheduler  
- conversation turns as text in / text out  
- a new adapter (e.g. Twilio) that converts call audio ↔ text and maps call SID → `VoiceSession`

That work is **proposed**, not implemented.

---

## Sample clinic data

`python manage.py seed_clinic` loads:

- Specialties: General Practice, Dermatology  
- Doctors: Dr. Amina Khan (GP), Dr. Bilal Hussain (GP), Dr. Sara Malik (Dermatology)  
- Working hours, one unavailability block, and several occupied slots  

Clinic timezone: **Asia/Karachi**. Slot length: **30 minutes**.

---

## HTTP surfaces

| Method | Path | Purpose |
|---|---|---|
| GET | `/` or `/voice/` | Free browser voice UI |
| GET | `/doctors/` | Doctors directory table |
| GET | `/doctors/<id>/` | Doctor detail + available booking slots |
| POST | `/voice/api/session/start/` | Start conversation |
| POST | `/voice/api/session/turn/` | Send user text |
| POST | `/voice/api/session/abandon/` | End without booking |
| GET | `/booking/api/specialties/` | List specialties |
| GET | `/booking/api/doctors/?specialty_id=` | Find doctors |
| GET | `/booking/api/availability/` | Free slots |
| POST | `/booking/api/appointments/` | Create booking (idempotent) |
| — | `/admin/` | Staff-only clinic records |

---

## Adding another AI/speech provider

Default path needs **no** external AI vendor. To add one later without rewriting booking:

1. Implement `AIProvider` in `ai/providers/<name>_provider.py` (`complete`, optional `transcribe` / `synthesize`).  
2. Register it in `ai/providers/registry.py` → `PROVIDER_CLASSES`.  
3. Set `AI_PROVIDER=<name>` and any secrets in `.env` (never commit keys).  
4. Keep `booking.services` and the conversation tools as the only writers of appointments.  
5. Prefer keeping browser Web Speech for STT/TTS unless you intentionally move media server-side.

Paid providers are **out of scope** for this assessment build.

---

## Reliability behaviours

| Requirement | How it is handled |
|---|---|
| Two requests for the same slot | DB unique constraint + `select_for_update`; one wins |
| Retry / duplicate submit | Same `idempotency_key` returns the original appointment |
| Slot taken mid-conversation | Re-check on confirm; offer alternatives; do not claim success |
| Unclear speech / incomplete datetime | Clarifying prompts; no guess booking |
| Backend failure | Honest message; `booked: false`; no fake reference |
| Abandoned / idle session | Status `abandoned`; no `Appointment` row |

---

## Assumptions

- One clinic, one timezone (`Asia/Karachi`), fixed 30-minute slots aligned to working hours.  
- English speech/text; Chrome gives the best free Web Speech support.  
- Specialty matching is keyword-based against specialty name/description (good enough for the demo corpus).  
- Patients are identified by name + contact number only.  
- Appointments exist only as `confirmed` or `cancelled` — never as unconfirmed drafts.  
- Demo data is synthetic.

---

## Limitations

- Web Speech quality and browser support vary; Safari/Firefox may need the text box.  
- Rule-based dialogue is narrower than a large language model for free-form chat.  
- Date/time understanding covers common phrases (`Monday at 10 AM`, ISO dates, today/tomorrow), not every locale idiom.  
- SQLite is fine for the prototype; production should use PostgreSQL for stronger concurrent write behaviour.  
- No authentication on the public voice UI (prototype). Admin is staff-gated.  
- No SMS/email notifications, cancellations UI, Google Calendar, or Twilio phone yet.

---

## Approximate operating costs

| Item | Cost for this prototype |
|---|---|
| Django + SQLite on a laptop | **$0** |
| Browser Web Speech / speechSynthesis | **$0** (browser features) |
| Paid LLM / Whisper / cloud TTS | **$0** (not used) |
| Optional future OpenAI-style usage | Not required; would be usage-based if added later |
| Production hosting (estimate) | Small VM or PaaS ~$5–20/month depending on traffic |
| Production Postgres | Often included or ~$0–15/month on small plans |

For the assessment demo, expect **$0 API spend**.

---

## Main technical decisions

1. **Backend authority over slots** — calendar + transactional create APIs, not the dialogue layer.  
2. **Free browser speech** — meets the “no paid keys” constraint while still delivering a spoken demo.  
3. **Rule-based conversation + tools** — predictable booking flow, easier to test than an unconstrained model.  
4. **No pending appointments** — abandoned chats cannot leave half-booked records.  
5. **Replaceable `ai` adapters** — keeps a clean seam if a provider is added later without touching booking.

### What I would improve before production

- PostgreSQL, stronger auth, rate limits, audit logs  
- Richer NLU or an optional local LLM (Ollama) behind the same adapter  
- Explicit hold/soft-lock window for a chosen slot during confirmation  
- Accessibility review, HTTPS, CSRF hardening for cookie-based clients  
- Observability (structured logs/metrics around booking failures)  
- Phone adapter with call lifecycle, still calling the same booking services  

---

## Optional extensions (status)

| Extension | Status |
|---|---|
| Browser voice booking (Web Speech) | **Implemented** |
| Twilio / telephone calls | **Proposed** |
| Google Calendar sync | **Proposed** |
| Cancellation / rescheduling APIs | **Proposed** (model supports `cancelled`) |
| Appointment notifications | **Proposed** |

---

## Project layout

```
config/           Django settings and root URLs
clinic/           Doctors, specialties, hours, patients, seed command
booking/          Calendar, services, booking APIs, reliability tests
voice/            Conversation, tools, Web Speech UI, failure handling
ai/               Free local/fake provider adapters
templates/voice/  Assistant page
static/voice/     CSS/JS for mic + speechSynthesis
```

---

## Demo tips

1. Seed data, start the server, open Chrome to `/`.  
2. Successful path: name → phone → reason like “cough” → Monday 10 AM (if free) → confirm **yes**.  
3. Unavailable path: book a slot first (or use a seed-occupied time), then ask for that time and accept an alternative.  
4. Show admin appointments afterward; show that abandoning mid-chat creates no appointment.
