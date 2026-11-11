"""
LiveKit Agents entry point — clinic voice booking pipeline (cloud AI stack).

  Orchestrator — LiveKit Agents (local WebRTC)
  STT          — Groq Whisper API (+ hallucination filter)
  Brain (LLM)  — Groq Cloud with multi-model failover
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
from livekit.plugins import silero

from voice_agent import tools as django_tools
from voice_agent.booking_state import BookingFacts
from voice_agent.llm_groq import build_groq_llm, groq_configured
from voice_agent.singleton import claim_singleton, health_port
from voice_agent.stt_filters import is_hallucinated_transcript
from voice_agent.stt_groq import build_groq_stt
from voice_agent.text_filters import strip_think_stream
from voice_agent.tts_fish_audio import build_fish_tts, load_fish_tts_config
from voice_agent.ui_events import publish_ui


logger = logging.getLogger("voice_agent.pipeline")

SYSTEM_INSTRUCTIONS = """
You are a clinic voice booking assistant (Asia/Karachi, ~30 min slots).
Only schedule. Never diagnose or prescribe.

TURN RULES
- Speak ONLY as the assistant. Never invent patient lines.
- 1–2 short clean sentences. Ask ONE question, then STOP.
- Never repeat or restart the same sentence mid-reply.
- Plain speech only (no markdown).
- Trust KNOWN FACTS injected each turn — do not re-ask those fields unless corrected.
- "Thank you" / "Don't know" for name: gently ask again for the full name.

FLOW
1) name → phone → reason → preferred day/time
2) match_specialty(reason); if ambiguous ask one clarifier then retry
3) find_doctors(specialty_id); if several, ask which doctor then wait
4) check_availability before offering any time — never invent slots
5) read back doctor/time/name/phone/reason; ask yes/no to confirm
6) create_appointment only after explicit yes; success only with booking_reference
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


TURN_SILENCE_SECONDS = _env_float("VOICE_TURN_SILENCE_SECONDS", 0.75)


def build_stt():
    return build_groq_stt()


def build_llm():
    return build_groq_llm(temperature=_env_float("GROQ_LLM_TEMPERATURE", 0.2))


def build_tts(*, voice=None):
    return build_fish_tts(voice=voice)


def build_vad():
    return silero.VAD.load(
        min_speech_duration=_env_float("VAD_MIN_SPEECH_SECONDS", 0.25),
        min_silence_duration=_env_float("VAD_MIN_SILENCE_SECONDS", 0.7),
        prefix_padding_duration=0.3,
        activation_threshold=_env_float("VAD_ACTIVATION_THRESHOLD", 0.65),
    )


def _maybe_capture_name_phone(facts: BookingFacts, text: str) -> None:
    """Lightly learn name/phone from clear user utterances."""
    raw = (text or "").strip()
    if not raw:
        return
    phone_match = re.search(r"(\+?\d[\d\s\-]{8,}\d)", raw)
    if phone_match and not facts.phone:
        digits = re.sub(r"\D", "", phone_match.group(1))
        if len(digits) >= 10:
            facts.phone = digits
    # "My name is X" / bare two-word name answers while name missing.
    if not facts.patient_name:
        name_match = re.search(
            r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\s'.-]{1,60})$",
            raw,
        )
        if name_match:
            facts.patient_name = name_match.group(1).strip(" .")
        elif re.fullmatch(r"[A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,3}", raw):
            if raw.lower() not in {"yes", "no", "yeah", "okay", "ok"}:
                facts.patient_name = raw


class BookingAgent(Agent):
    """Conversational booking agent: Groq LLM brain + Django tools + UI cards."""

    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)
        self.facts = BookingFacts()

    async def on_enter(self) -> None:
        await self.session.say(GREETING, allow_interruptions=False)

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = ""
        try:
            text = (new_message.text_content or "").strip()
        except Exception:  # noqa: BLE001
            text = str(getattr(new_message, "content", "")).strip()

        if is_hallucinated_transcript(text):
            logger.info("ignoring hallucinated STT text=%r", text[:120])
            raise StopResponse()

        logger.info("user turn completed text=%r", text[:200])
        _maybe_capture_name_phone(self.facts, text)

        try:
            turn_ctx.truncate(max_items=MAX_CHAT_ITEMS)
            self.chat_ctx.truncate(max_items=MAX_CHAT_ITEMS)
        except Exception:  # noqa: BLE001
            logger.exception("chat truncate failed")

        turn_ctx.add_message(role="system", content=self.facts.snapshot_message())

    def tts_node(self, text, model_settings):
        return Agent.default.tts_node(self, strip_think_stream(text), model_settings)

    def transcription_node(self, text, model_settings):
        return Agent.default.transcription_node(
            self, strip_think_stream(text), model_settings
        )

    @function_tool()
    async def match_specialty(self, context: RunContext, reason: str) -> dict:
        """Match visit reason to a clinic specialty. Call after you have the reason.

        Args:
            reason: Brief reason for the visit in the patient's words.
        """
        if reason:
            self.facts.reason = reason.strip()
        result = django_tools.match_specialty(reason)
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
    async def find_doctors(
        self,
        context: RunContext,
        specialty_id: Optional[int] = None,
        specialty_query: str = "",
    ) -> dict:
        """List doctors for a specialty. Prefer specialty_id from match_specialty.

        Args:
            specialty_id: Specialty id from match_specialty.
            specialty_query: Optional free-text fallback if id is unknown.
        """
        sid = specialty_id if specialty_id is not None else self.facts.specialty_id
        result = django_tools.find_doctors(
            specialty_id=sid,
            specialty_query=specialty_query,
        )
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
    async def check_availability(
        self,
        context: RunContext,
        doctor_id: int,
        date: str,
        preferred_starts_at: str = "",
    ) -> dict:
        """Check real open slots. Always call before offering a specific time.

        Args:
            doctor_id: Doctor id from find_doctors.
            date: Preferred day as YYYY-MM-DD.
            preferred_starts_at: Optional ISO-8601 datetime the patient asked for.
        """
        preferred = preferred_starts_at or ""
        result = django_tools.check_availability(
            doctor_id=doctor_id,
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
        await publish_ui(
            "slots",
            doctor_id=doctor_id,
            doctor_name=self.facts.doctor_name,
            date=date,
            preferred_starts_at=preferred or None,
            preferred_available=result.get("preferred_available"),
            slots=slots[:8],
            status=result.get("status"),
            message=result.get("message"),
        )
        if preferred and result.get("preferred_available") is True:
            await publish_ui(
                "confirmation",
                patient_name=self.facts.patient_name,
                phone=self.facts.phone,
                doctor_name=self.facts.doctor_name,
                doctor_id=doctor_id,
                slot_start=preferred,
                reason=self.facts.reason,
            )
        return result

    @function_tool()
    async def create_appointment(
        self,
        context: RunContext,
        patient_name: str,
        phone: str,
        doctor_id: int,
        slot_start: str,
        reason: str,
        idempotency_key: str = "",
    ) -> dict:
        """Book ONLY after the patient explicitly says yes.

        Args:
            patient_name: Patient full name.
            phone: Patient contact number.
            doctor_id: Doctor id.
            slot_start: Chosen slot start as ISO-8601 datetime.
            reason: Brief reason for visit.
            idempotency_key: Unique key for this confirmation (generate one if empty).
        """
        # Prefer remembered facts when the model drifts.
        name = (patient_name or self.facts.patient_name or "").strip()
        phone_n = re.sub(r"\D", "", phone or self.facts.phone or "")
        reason_n = (reason or self.facts.reason or "").strip()
        doc_id = doctor_id or self.facts.doctor_id
        slot = slot_start or self.facts.slot_start or ""

        await publish_ui(
            "confirmation",
            patient_name=name,
            phone=phone_n,
            doctor_name=self.facts.doctor_name,
            doctor_id=doc_id,
            slot_start=slot,
            reason=reason_n,
        )

        key = (idempotency_key or "").strip() or "voice-{}".format(uuid.uuid4().hex)
        result = django_tools.create_appointment(
            patient_name=name,
            phone=phone_n,
            doctor_id=int(doc_id),
            slot_start=slot,
            reason=reason_n,
            idempotency_key=key,
        )
        self.facts.patient_name = name
        self.facts.phone = phone_n
        self.facts.reason = reason_n
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
        return result


def _prewarm(proc: JobProcess) -> None:
    logger.info("prewarming job process (Silero VAD; AI is Groq + Fish)")
    proc.userdata["vad"] = build_vad()
    if not groq_configured():
        logger.warning("GROQ_API_KEY not set; STT/LLM will fail until configured")
    if not load_fish_tts_config():
        logger.warning("FISH_API_KEY not set; TTS will fail until configured")
    logger.info("prewarm complete")


server = AgentServer(
    setup_fnc=_prewarm,
    initialize_process_timeout=60.0,
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
    build_stt()
    build_llm()
    if not load_fish_tts_config():
        logger.warning("FISH_API_KEY missing — set it in .env before connecting /voice/")
    import sys

    if len(sys.argv) == 1:
        sys.argv.append("start")
    logger.info("worker log file: %s", log_path)
    logger.info(
        "stack: LiveKit + Groq STT + Groq LLM failover + Django tools + Fish TTS"
    )
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
