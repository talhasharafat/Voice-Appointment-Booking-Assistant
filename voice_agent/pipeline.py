"""
LiveKit Agents entry point — clinic voice booking pipeline.

  Orchestrator — LiveKit Agents (local WebRTC)
  STT          — Faster-Whisper local (+ hallucination filter)
  Brain (LLM)  — DeepSeek API (``deepseek-flash``)
  Tools        — Django HTTP API + structured UI cards
  TTS          — Fish Audio ``fish-audio/s2.1-pro-free`` (mp3)

Run:
  ./scripts/start_voice_agent.sh
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_AGENT_DIR = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)
load_dotenv(_AGENT_DIR / ".env", override=True)

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobProcess,
    RunContext,
    TurnHandlingOptions,
    function_tool,
    room_io,
)
from livekit.agents.llm import StopResponse
from livekit.agents import stt as lk_stt
from livekit.plugins import silero

from voice_agent import tools as django_tools
from voice_agent.booking_state import BookingFacts
from voice_agent.llm_deepseek import build_deepseek_llm, deepseek_configured
from voice_agent.singleton import claim_singleton, health_port
from voice_agent.stt_filters import is_hallucinated_transcript
from voice_agent.stt_faster_whisper import build_faster_whisper_stt
from voice_agent.text_filters import strip_think_stream
from voice_agent.tts_fish_audio import build_fish_tts, load_fish_tts_config
from voice_agent.ui_events import publish_ui
from voice_agent.utterance import (
    digits_from_utterance,
    looks_like_reason,
    match_doctor_from_text,
    slot_start_from_choice,
)


logger = logging.getLogger("voice_agent.pipeline")

SYSTEM_INSTRUCTIONS = """
You are a clinic voice booking assistant (Asia/Karachi, ~30 min slots).
Only schedule. Never diagnose or prescribe.

HARD MEMORY RULES
- Trust KNOWN FACTS every turn. If phone/name/reason/doctor/slot is listed, it is already saved.
- NEVER ask again for a locked field (especially phone and name).
- After create_appointment succeeds, the booking is FINAL for this call.
- If a booking reference exists: tell the patient it is confirmed, that re-editing is
  not available, and stop. No more questions or tools. Never change saved details.

TURN RULES
- Speak ONLY as the assistant. Never invent patient lines.
- 1–2 short clean sentences. Ask ONE question, then STOP.
- Plain speech only (no markdown).
- Dizzy/fever/cough/pain → General Practice. Skin/rash/hair/nail → Dermatology.
- After one unclear specialty clarifier, default to General Practice.

TOOLS (keep arguments minimal)
1) match_specialty(reason)
2) find_doctors()  — uses saved specialty
3) check_availability(date)  — date as YYYY-MM-DD; uses saved doctor
4) create_appointment(confirmed=true)  — only after patient says yes; uses saved facts
""".strip()

GREETING = (
    "Hello. I can help you book a clinic appointment. "
    "I do not diagnose or prescribe. What is your full name?"
)

MAX_CHAT_ITEMS = int(os.getenv("VOICE_MAX_CHAT_ITEMS", "14") or 14)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


TURN_SILENCE_SECONDS = _env_float("VOICE_TURN_SILENCE_SECONDS", 0.85)


def build_stt():
    return build_faster_whisper_stt()


def build_llm():
    return build_deepseek_llm(temperature=_env_float("DEEPSEEK_TEMPERATURE", 0.2))


def build_tts(*, voice=None):
    return build_fish_tts(voice=voice)


def build_vad():
    # Stricter VAD reduces silence → Whisper inventing phantom user lines.
    return silero.VAD.load(
        min_speech_duration=_env_float("VAD_MIN_SPEECH_SECONDS", 0.35),
        min_silence_duration=_env_float("VAD_MIN_SILENCE_SECONDS", 0.8),
        prefix_padding_duration=0.25,
        activation_threshold=_env_float("VAD_ACTIVATION_THRESHOLD", 0.72),
    )


def _maybe_capture_facts(facts: BookingFacts, text: str) -> None:
    """Deterministic memory — do not rely on the LLM to remember."""
    raw = (text or "").strip()
    if not raw:
        return

    digits = digits_from_utterance(raw)
    if not digits:
        phone_match = re.search(r"(\+?\d[\d\s\-]{8,}\d)", raw)
        if phone_match:
            digits = re.sub(r"\D", "", phone_match.group(1))
    if len(digits) >= 10:
        facts.phone = digits

    if not facts.patient_name:
        name_match = re.search(
            r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\s'.-]{1,60})$",
            raw,
        )
        if name_match:
            candidate = name_match.group(1).strip(" .")
            if not looks_like_reason(candidate):
                facts.patient_name = candidate
        elif re.fullmatch(r"[A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,3}", raw):
            low = raw.lower()
            if low not in {"yes", "no", "yeah", "okay", "ok"} and not looks_like_reason(raw):
                facts.patient_name = raw

    if not facts.reason and looks_like_reason(raw):
        cleaned = re.sub(r"(?i)^(i don'?t know[.!]?\s*)+", "", raw).strip(" .")
        if cleaned:
            facts.reason = cleaned

    if facts.doctor_id is None and facts.offered_doctors:
        doc = match_doctor_from_text(raw, facts.offered_doctors)
        if doc and doc.get("id") is not None:
            facts.doctor_id = int(doc["id"])
            facts.doctor_name = str(doc.get("name") or "")

    if not facts.slot_start and facts.offered_slots:
        chosen = slot_start_from_choice(raw, facts.offered_slots)
        if chosen:
            facts.slot_start = chosen


class BookingAgent(Agent):
    """Conversational booking agent: DeepSeek LLM + Django tools + UI cards."""

    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)
        self.facts = BookingFacts()

    async def on_enter(self) -> None:
        await self.session.say(GREETING, allow_interruptions=False)

    async def stt_node(self, audio, model_settings):
        """Drop hallucinated Whisper text before it reaches the UI or the LLM."""

        async def _filtered():
            async for event in Agent.default.stt_node(self, audio, model_settings):
                if isinstance(event, str):
                    if is_hallucinated_transcript(event):
                        logger.info("drop hallucinated STT string=%r", event[:120])
                        continue
                    yield event
                    continue

                if event.type in (
                    lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
                    lk_stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    getattr(lk_stt.SpeechEventType, "PREFLIGHT_TRANSCRIPT", None),
                ):
                    text = ""
                    if event.alternatives:
                        text = (event.alternatives[0].text or "").strip()
                    if text and is_hallucinated_transcript(text):
                        logger.info(
                            "drop hallucinated STT event type=%s text=%r",
                            event.type,
                            text[:120],
                        )
                        continue
                yield event

        return _filtered()

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = ""
        try:
            text = (new_message.text_content or "").strip()
        except Exception:  # noqa: BLE001
            text = str(getattr(new_message, "content", "")).strip()

        if is_hallucinated_transcript(text):
            logger.info("ignoring hallucinated STT text=%r", text[:120])
            raise StopResponse()

        # After confirmation: refuse edits deterministically (do not rely on the LLM).
        if self.facts.is_confirmed():
            reply = self.facts.post_confirm_reply()
            logger.info(
                "post-confirm lock; ignoring edit attempt text=%r ref=%s",
                text[:200],
                self.facts.booking_reference,
            )
            await self.session.say(reply, allow_interruptions=False)
            raise StopResponse()

        logger.info(
            "user turn completed text=%r facts_phone=%s next=%s",
            text[:200],
            self.facts.phone,
            self.facts.next_missing(),
        )
        _maybe_capture_facts(self.facts, text)
        logger.info(
            "facts after capture name=%r phone=%s reason=%r doctor=%s slot=%s next=%s",
            self.facts.patient_name,
            self.facts.phone,
            (self.facts.reason or "")[:80],
            self.facts.doctor_id,
            self.facts.slot_start,
            self.facts.next_missing(),
        )

        try:
            turn_ctx.truncate(max_items=MAX_CHAT_ITEMS)
        except Exception:  # noqa: BLE001
            logger.exception("chat truncate failed")

        turn_ctx.add_message(role="system", content=self.facts.snapshot_message())

    def tts_node(self, text, model_settings):
        return Agent.default.tts_node(self, strip_think_stream(text), model_settings)

    def transcription_node(self, text, model_settings):
        return Agent.default.transcription_node(
            self, strip_think_stream(text), model_settings
        )

    def _reject_if_confirmed(self) -> Optional[dict]:
        if not self.facts.is_confirmed():
            return None
        return {
            "ok": False,
            "error": {
                "code": "booking_locked",
                "message": (
                    "Booking already confirmed (reference {}). "
                    "Re-editing is not available.".format(self.facts.booking_reference)
                ),
            },
            "assistant_next_line": self.facts.post_confirm_reply(),
            "stop_booking_flow": True,
        }

    @function_tool()
    async def match_specialty(self, context: RunContext, reason: str) -> dict:
        """Match visit reason to a clinic specialty. Call after you have the reason.

        Args:
            reason: Brief reason for the visit in the patient's words.
        """
        locked = self._reject_if_confirmed()
        if locked:
            return locked
        if reason:
            self.facts.reason = reason.strip()
        result = django_tools.match_specialty(reason or self.facts.reason or "")
        self.facts.apply_match(result)
        await publish_ui(
            "specialty",
            status=result.get("status"),
            specialty_name=result.get("specialty_name"),
            clarifying_question=result.get("clarifying_question"),
            matches=result.get("matches") or [],
            message=result.get("message"),
        )
        return result

    @function_tool()
    async def find_doctors(self, context: RunContext) -> dict:
        """List doctors for the specialty already matched in this session. No arguments."""
        locked = self._reject_if_confirmed()
        if locked:
            return locked
        sid = self.facts.specialty_id
        if sid is None:
            return {
                "ok": False,
                "error": {
                    "code": "missing_specialty",
                    "message": "Call match_specialty first.",
                },
            }
        result = django_tools.find_doctors(specialty_id=sid, specialty_query="")
        self.facts.apply_doctors(result)
        doctors = result.get("doctors") or []
        await publish_ui(
            "doctors",
            specialty_id=result.get("specialty_id") or sid,
            specialty_name=self.facts.specialty_name,
            doctors=[
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "next_open_slots": d.get("next_open_slots") or [],
                }
                for d in doctors
            ],
            message=result.get("message"),
        )
        return result

    @function_tool()
    async def check_availability(self, context: RunContext, date: str) -> dict:
        """Check open slots for the selected doctor on a calendar day.

        Args:
            date: Preferred day as YYYY-MM-DD.
        """
        locked = self._reject_if_confirmed()
        if locked:
            return locked
        doctor_id = self.facts.doctor_id
        if doctor_id is None:
            return {
                "ok": False,
                "error": {
                    "code": "missing_doctor",
                    "message": "Ask which doctor first, then call check_availability.",
                },
            }
        preferred = self.facts.slot_start or ""
        result = django_tools.check_availability(
            doctor_id=int(doctor_id),
            date=date,
            preferred_starts_at=preferred or None,
        )
        self.facts.apply_availability(result, preferred=preferred)
        doctor = result.get("doctor") or {}
        if doctor.get("name"):
            self.facts.doctor_name = doctor.get("name")
        if doctor.get("id") is not None:
            self.facts.doctor_id = int(doctor["id"])
        slots = result.get("alternatives") or result.get("slots") or []
        numbered = []
        for i, slot in enumerate(slots[:8], start=1):
            if isinstance(slot, dict):
                item = dict(slot)
                item["list_number"] = i
                numbered.append(item)
            else:
                numbered.append({"list_number": i, "starts_at": slot})
        if numbered:
            result = dict(result)
            result["numbered_slots"] = numbered
            result["instruction"] = (
                "When the patient chooses a list number, remember that slot. "
                "Do not default to slot 1."
            )
        await publish_ui(
            "slots",
            doctor_id=int(doctor_id),
            doctor_name=self.facts.doctor_name,
            date=date,
            preferred_starts_at=preferred or None,
            preferred_available=result.get("preferred_available"),
            slots=slots[:8],
            status=result.get("status"),
            message=result.get("message"),
        )
        if self.facts.slot_start:
            await publish_ui(
                "confirmation",
                patient_name=self.facts.patient_name,
                phone=self.facts.phone,
                doctor_name=self.facts.doctor_name,
                doctor_id=int(doctor_id),
                slot_start=self.facts.slot_start,
                reason=self.facts.reason,
            )
        return result

    @function_tool()
    async def create_appointment(self, context: RunContext, confirmed: bool) -> dict:
        """Book using saved session facts. Call only after the patient says yes.

        Args:
            confirmed: Must be true after an explicit patient yes.
        """
        locked = self._reject_if_confirmed()
        if locked:
            return locked
        if not confirmed:
            return {
                "ok": False,
                "error": {
                    "code": "not_confirmed",
                    "message": "Patient has not confirmed. Ask yes/no first.",
                },
            }

        name = (self.facts.patient_name or "").strip()
        phone_n = re.sub(r"\D", "", self.facts.phone or "")
        reason_n = (self.facts.reason or "").strip()
        doc_id = self.facts.doctor_id
        slot = (self.facts.slot_start or "").strip()
        if not slot and self.facts.offered_slots:
            first = self.facts.offered_slots[0]
            if isinstance(first, dict):
                slot = str(first.get("starts_at") or first.get("start") or "")
            else:
                slot = str(first)

        missing = []
        if not name:
            missing.append("name")
        if len(phone_n) < 10:
            missing.append("phone")
        if doc_id is None:
            missing.append("doctor")
        if not slot:
            missing.append("slot")
        if not reason_n:
            missing.append("reason")
        if missing:
            return {
                "ok": False,
                "error": {
                    "code": "missing_facts",
                    "message": "Cannot book yet; still missing: {}.".format(
                        ", ".join(missing)
                    ),
                },
            }

        await publish_ui(
            "confirmation",
            patient_name=name,
            phone=phone_n,
            doctor_name=self.facts.doctor_name,
            doctor_id=doc_id,
            slot_start=slot,
            reason=reason_n,
        )

        key = "voice-{}".format(uuid.uuid4().hex)
        result = django_tools.create_appointment(
            patient_name=name,
            phone=phone_n,
            doctor_id=int(doc_id),
            slot_start=slot,
            reason=reason_n,
            idempotency_key=key,
        )
        self.facts.apply_booking(result)
        if result.get("ok"):
            await publish_ui(
                "booking",
                booking_reference=result.get("booking_reference"),
                patient_name=name,
                phone=phone_n,
                doctor_name=self.facts.doctor_name,
                slot_start=result.get("starts_at") or slot,
                reason=reason_n,
                message="Appointment confirmed.",
            )
            result = dict(result)
            result["assistant_next_line"] = (
                "Your appointment is confirmed. Your booking reference is {}. "
                "Changing details after confirmation is not available. Goodbye.".format(
                    result.get("booking_reference")
                )
            )
            result["stop_booking_flow"] = True
        return result


def _prewarm(proc: JobProcess) -> None:
    logger.info("prewarming job process (Silero VAD; Faster-Whisper + DeepSeek + Fish)")
    proc.userdata["vad"] = build_vad()
    # Do not eagerly load Whisper here — it can OOM/kill the job subprocess on CPU Macs.
    if not deepseek_configured():
        logger.warning(
            "DEEPSEEK_API_KEY not set — add it to .env "
            "(https://platform.deepseek.com)"
        )
    if not load_fish_tts_config():
        logger.warning("FISH_API_KEY not set; TTS will fail until configured")
    logger.info("prewarm complete")


server = AgentServer(
    setup_fnc=_prewarm,
    initialize_process_timeout=120.0,
    num_idle_processes=1,
    port=health_port(),
)


@server.rtc_session(agent_name="clinic-booking")
async def clinic_booking(ctx: agents.JobContext):
    vad = ctx.proc.userdata.get("vad") or build_vad()
    session = AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        vad=vad,
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            endpointing={
                "mode": "fixed",
                "min_delay": TURN_SILENCE_SECONDS,
                "max_delay": max(TURN_SILENCE_SECONDS, 3.0),
            },
            # Facts injection in on_user_turn_completed invalidates preemptive
            # generation and wastes an LLM round-trip if left enabled.
            preemptive_generation={"enabled": False},
        ),
    )

    await session.start(
        room=ctx.room,
        agent=BookingAgent(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(),
            audio_output=room_io.AudioOutputOptions(),
            text_output=True,
        ),
    )


def main() -> None:
    log_level = os.getenv("VOICE_AGENT_LOG_LEVEL", "INFO")
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent.log"
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    claim_singleton()
    # Resolve LLM config early (does not load heavy weights). Whisper loads on first use.
    build_llm()
    if not load_fish_tts_config():
        logger.warning("FISH_API_KEY missing — set it in .env before connecting /voice/")
    import sys

    if len(sys.argv) == 1:
        sys.argv.append("start")
    logger.info("worker log file: %s", log_path)
    logger.info(
        "stack: LiveKit + Faster-Whisper STT + DeepSeek LLM + Django tools + Fish TTS"
    )
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
