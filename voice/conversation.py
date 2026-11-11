"""
Free rule-based booking conversation.

Speech arrives as text from the browser Web Speech API. This engine collects
details, calls voice.tools for specialty/doctors/slots/booking, reads back the
plan, and only then creates an appointment.
"""

import logging

from django.db import transaction

from voice import parsing
from voice.failures import ConversationFault, expire_if_stale, honest_backend_failure
from voice.formatting import doctors_list_message, format_slot_label, slots_list_message
from voice.models import VoiceSession
from voice.tools import run_tool

logger = logging.getLogger(__name__)

GREETING = (
    "Hello. I can help you book a clinic appointment. I do not diagnose or prescribe. "
    "What is your full name?"
)


def start_session():
    session = VoiceSession.objects.create(
        state="ASK_NAME",
        data={},
        last_assistant_message=GREETING,
        status=VoiceSession.Status.ACTIVE,
    )
    return _payload(session, GREETING, expect="name")


@transaction.atomic
def abandon_session(session_id):
    session = VoiceSession.objects.select_for_update().filter(pk=session_id).first()
    if session is None:
        return {"ok": False, "error": {"code": "not_found", "message": "Session not found."}}
    if session.status == VoiceSession.Status.ACTIVE:
        session.state = "ABANDONED"
        session.last_assistant_message = "Session ended. No appointment was booked."
        session.save(update_fields=["state", "last_assistant_message", "updated_at"])
        session.mark_closed(VoiceSession.Status.ABANDONED)
    return _payload(session, session.last_assistant_message, expect=None)


@transaction.atomic
def process_turn(session_id, user_text):
    session = VoiceSession.objects.select_for_update().filter(pk=session_id).first()
    if session is None:
        return {"ok": False, "error": {"code": "not_found", "message": "Session not found."}}
    if expire_if_stale(session):
        return _payload(session, session.last_assistant_message, expect=None, ok=True)
    if session.status != VoiceSession.Status.ACTIVE:
        return _payload(
            session,
            "This conversation is already closed. No appointment was booked unless you already received a reference. Start a new chat to book.",
            expect=None,
            ok=True,
        )

    text = (user_text or "").strip()
    if parsing.is_unclear(text):
        reply = "I did not catch that. " + _prompt_for_state(session)
        session.last_assistant_message = reply
        session.save(update_fields=["last_assistant_message", "updated_at"])
        return _payload(session, reply, expect=_expect_for_state(session.state))

    if parsing.looks_like_diagnosis_request(text):
        reply = (
            "I am only a scheduling assistant and cannot diagnose or prescribe. "
            + _prompt_for_state(session)
        )
        session.last_assistant_message = reply
        session.save(update_fields=["last_assistant_message", "updated_at"])
        return _payload(session, reply, expect=_expect_for_state(session.state))

    handlers = {
        "ASK_NAME": _handle_name,
        "ASK_PHONE": _handle_phone,
        "ASK_REASON": _handle_reason,
        "CLARIFY_SPECIALTY": _handle_specialty_clarification,
        "ASK_DOCTOR": _handle_doctor_choice,
        "ASK_DATETIME": _handle_datetime,
        "OFFER_ALTERNATIVES": _handle_alternative_choice,
        "CONFIRM": _handle_confirmation,
    }
    handler = handlers.get(session.state)
    if handler is None:
        session.state = "FAILED"
        session.last_assistant_message = honest_backend_failure("invalid conversation state")
        session.save(update_fields=["state", "last_assistant_message", "updated_at"])
        session.mark_closed(VoiceSession.Status.FAILED)
        return _payload(session, session.last_assistant_message, expect=None)

    try:
        reply, expect = handler(session, text)
    except ConversationFault as exc:
        logger.warning("conversation fault session=%s code=%s", session.id, exc.code)
        reply = exc.message
        expect = _expect_for_state(session.state)
        session.last_assistant_message = reply
        session.save(update_fields=["last_assistant_message", "data", "state", "updated_at"])
        return _payload(session, reply, expect=expect, ok=True)
    except Exception:
        logger.exception("unexpected conversation failure session=%s", session.id)
        reply = honest_backend_failure()
        session.last_assistant_message = reply
        # Stay active when possible so the patient can retry without a false booking.
        if session.state == "CONFIRM":
            session.state = "ASK_DATETIME"
        session.save(update_fields=["last_assistant_message", "state", "data", "updated_at"])
        return _payload(session, reply, expect=_expect_for_state(session.state), ok=True)

    session.last_assistant_message = reply
    session.save()
    return _payload(session, reply, expect=expect)


def _payload(session, message, expect, ok=True):
    reference = session.booking_reference or session.data.get("booking_reference")
    completed = session.state == "COMPLETED" and bool(reference)
    return {
        "ok": ok,
        "session_id": str(session.id),
        "status": session.status,
        "state": session.state,
        "message": message,
        "expect": expect,
        "booked": completed,
        "data": {
            "patient_name": session.data.get("patient_name"),
            "contact_number": session.data.get("contact_number"),
            "reason": session.data.get("reason"),
            "specialty_name": session.data.get("specialty_name"),
            "doctor_name": session.data.get("doctor_name"),
            "starts_at": session.data.get("starts_at"),
            "booking_reference": reference if completed else None,
        },
    }


def _prompt_for_state(session):
    prompts = {
        "ASK_NAME": "What is your full name?",
        "ASK_PHONE": "What is your contact number?",
        "ASK_REASON": "What is the brief reason for your visit?",
        "CLARIFY_SPECIALTY": session.data.get("clarifying_question")
        or "Which specialty fits better?",
        "ASK_DOCTOR": "Which doctor would you like?",
        "ASK_DATETIME": "What day and time do you prefer? For example, Monday at 10 AM.",
        "OFFER_ALTERNATIVES": "Please choose one of the available times I listed.",
        "CONFIRM": "Please say yes to confirm the booking, or no to cancel.",
    }
    return prompts.get(session.state, "Please continue.")


def _expect_for_state(state):
    return {
        "ASK_NAME": "name",
        "ASK_PHONE": "phone",
        "ASK_REASON": "reason",
        "CLARIFY_SPECIALTY": "specialty_choice",
        "ASK_DOCTOR": "doctor_choice",
        "ASK_DATETIME": "datetime",
        "OFFER_ALTERNATIVES": "slot_choice",
        "CONFIRM": "yes_no",
    }.get(state)


def _handle_name(session, text):
    name = parsing.extract_name(text)
    if not name:
        return "Please tell me your full name.", "name"
    session.data["patient_name"] = name
    session.state = "ASK_PHONE"
    session.data = dict(session.data)
    return "Thanks, {}. What is your contact number?".format(name), "phone"


def _handle_phone(session, text):
    phone = parsing.extract_phone(text)
    if not phone:
        return (
            "I need a contact number with at least 10 digits. Please say or type the number.",
            "phone",
        )
    session.data["contact_number"] = phone
    session.state = "ASK_REASON"
    session.data = dict(session.data)
    return "Got it. What is the brief reason for your visit?", "reason"


def _handle_reason(session, text):
    reason = text.strip()
    if len(reason) < 3:
        return "Please share a short reason for the visit.", "reason"
    session.data["reason"] = reason
    matched = run_tool("match_specialty", {"reason": reason})
    result = matched.get("result") or {}
    if matched.get("ok") and result.get("status") == "matched":
        return _after_specialty_selected(
            session,
            specialty_id=result["specialty_id"],
            specialty_name=result["specialty_name"],
        )
    question = result.get("clarifying_question") or "Is this a general illness, or a skin concern?"
    session.data["clarifying_question"] = question
    session.data["specialty_options"] = result.get("matches") or []
    session.state = "CLARIFY_SPECIALTY"
    session.data = dict(session.data)
    return question, "specialty_choice"


def _handle_specialty_clarification(session, text):
    cleaned = parsing.normalize_text(text)
    options = session.data.get("specialty_options") or []
    # Re-run matcher with clarification text combined with reason.
    combined = "{} {}".format(session.data.get("reason", ""), text)
    matched = run_tool("match_specialty", {"reason": combined})
    result = matched.get("result") or {}
    if matched.get("ok") and result.get("status") == "matched":
        return _after_specialty_selected(
            session,
            specialty_id=result["specialty_id"],
            specialty_name=result["specialty_name"],
        )
    for option in options:
        if option["name"].lower() in cleaned:
            return _after_specialty_selected(
                session,
                specialty_id=option["specialty_id"],
                specialty_name=option["name"],
            )
    if "skin" in cleaned or "rash" in cleaned or "derm" in cleaned:
        for option in options:
            if "derm" in option["name"].lower():
                return _after_specialty_selected(
                    session,
                    specialty_id=option["specialty_id"],
                    specialty_name=option["name"],
                )
        rematch = run_tool("match_specialty", {"reason": "rash skin " + session.data.get("reason", "")})
        rem = rematch.get("result") or {}
        if rematch.get("ok") and rem.get("status") == "matched":
            return _after_specialty_selected(session, rem["specialty_id"], rem["specialty_name"])
    if "general" in cleaned or "fever" in cleaned or "cough" in cleaned:
        rematch = run_tool("match_specialty", {"reason": "cough fever " + session.data.get("reason", "")})
        rem = rematch.get("result") or {}
        if rematch.get("ok") and rem.get("status") == "matched":
            return _after_specialty_selected(session, rem["specialty_id"], rem["specialty_name"])
    question = session.data.get("clarifying_question") or "Which specialty should we use?"
    return "Still unclear. " + question, "specialty_choice"


def _after_specialty_selected(session, specialty_id, specialty_name):
    session.data["specialty_id"] = specialty_id
    session.data["specialty_name"] = specialty_name
    doctors_resp = run_tool("find_doctors", {"specialty_id": specialty_id})
    doctors = (doctors_resp.get("result") or {}).get("doctors") or []
    session.data["doctors"] = doctors
    if not doctors:
        session.state = "ASK_REASON"
        session.data = dict(session.data)
        return (
            "I matched {}, but no doctors are available. Please share another reason.".format(
                specialty_name
            ),
            "reason",
        )
    if len(doctors) == 1:
        doctor = doctors[0]
        session.data["doctor_id"] = doctor["id"]
        session.data["doctor_name"] = doctor["name"]
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            "I will book with {}. What day and time do you prefer?".format(doctor["name"]),
            "datetime",
        )
    session.state = "ASK_DOCTOR"
    session.data = dict(session.data)
    return doctors_list_message(specialty_name, doctors), "doctor_choice"


def _handle_doctor_choice(session, text):
    cleaned = parsing.normalize_text(text)
    doctors = session.data.get("doctors") or []
    for index, doctor in enumerate(doctors, start=1):
        if str(index) in cleaned.split() or doctor["name"].lower() in cleaned:
            session.data["doctor_id"] = doctor["id"]
            session.data["doctor_name"] = doctor["name"]
            session.state = "ASK_DATETIME"
            session.data = dict(session.data)
            return "What day and time do you prefer with {}?".format(doctor["name"]), "datetime"
    # partial last-name match
    for doctor in doctors:
        last = doctor["name"].split()[-1].lower()
        if last and last in cleaned:
            session.data["doctor_id"] = doctor["id"]
            session.data["doctor_name"] = doctor["name"]
            session.state = "ASK_DATETIME"
            session.data = dict(session.data)
            return "What day and time do you prefer with {}?".format(doctor["name"]), "datetime"
    return (
        doctors_list_message(session.data.get("specialty_name") or "this specialty", doctors),
        "doctor_choice",
    )


def _handle_datetime(session, text):
    missing = parsing.datetime_missing_parts(text)
    if missing:
        if missing == ["date"]:
            return (
                "I heard a time but not a day. Please include the day, for example Monday at 10 AM.",
                "datetime",
            )
        if missing == ["time"]:
            return (
                "I heard a day but not a time. Please include the time, for example Monday at 10 AM.",
                "datetime",
            )
        return (
            "I could not understand the date and time. Please try like Monday at 10 AM.",
            "datetime",
        )
    preferred = parsing.parse_preferred_datetime(text)
    if preferred is None:
        return (
            "I could not understand the date and time. Please try like Monday at 10 AM.",
            "datetime",
        )
    return _offer_or_confirm_slot(session, preferred.isoformat())


def _offer_or_confirm_slot(session, starts_at_iso):
    doctor_id = session.data.get("doctor_id")
    day = starts_at_iso[:10]
    availability = run_tool(
        "check_availability",
        {
            "doctor_id": doctor_id,
            "date": day,
            "preferred_starts_at": starts_at_iso,
        },
    )
    if not availability.get("ok"):
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            "I could not check availability right now. Please say another day and time.",
            "datetime",
        )
    result = availability["result"]
    if result.get("preferred_available"):
        session.data["starts_at"] = starts_at_iso
        session.data["offered_slots"] = []
        session.state = "CONFIRM"
        session.data = dict(session.data)
        return _readback(session), "yes_no"

    alternatives = result.get("alternatives") or result.get("slots") or []
    if not alternatives:
        from datetime import datetime, timedelta

        start_day = datetime.fromisoformat(starts_at_iso).date()
        widened = run_tool(
            "check_availability",
            {
                "doctor_id": doctor_id,
                "date_from": start_day.isoformat(),
                "date_to": (start_day + timedelta(days=3)).isoformat(),
            },
        )
        alternatives = (widened.get("result") or {}).get("slots") or []

    if not alternatives:
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            "That time is unavailable and I could not find nearby open slots. "
            "Please choose another day and time.",
            "datetime",
        )

    session.data["offered_slots"] = alternatives[:5]
    session.state = "OFFER_ALTERNATIVES"
    session.data = dict(session.data)
    return (
        slots_list_message(
            session.data["offered_slots"],
            intro="That time is not available. Here are other open times:",
        ),
        "slot_choice",
    )


def _handle_alternative_choice(session, text):
    cleaned = parsing.normalize_text(text)
    slots = session.data.get("offered_slots") or []
    for index, slot in enumerate(slots, start=1):
        if str(index) in cleaned.split() or "option {}".format(index) in cleaned:
            session.data["starts_at"] = slot["starts_at"]
            session.state = "CONFIRM"
            session.data = dict(session.data)
            return _readback(session), "yes_no"
    preferred = parsing.parse_preferred_datetime(text)
    if preferred is not None:
        return _offer_or_confirm_slot(session, preferred.isoformat())
    return "Please choose one of the listed option numbers.", "slot_choice"


def _readback(session):
    return (
        "Please confirm this booking:\n\n"
        "• Patient: {name}\n"
        "• Phone: {phone}\n"
        "• Reason: {reason}\n"
        "• Doctor: {doctor}\n"
        "• Time: {starts}\n\n"
        "Say yes to book, or no to cancel."
    ).format(
        name=session.data.get("patient_name"),
        phone=session.data.get("contact_number"),
        reason=session.data.get("reason"),
        doctor=session.data.get("doctor_name"),
        starts=format_slot_label(session.data.get("starts_at")),
    )


def _handle_confirmation(session, text):
    if parsing.is_no(text):
        session.state = "ABANDONED"
        session.data = dict(session.data)
        session.save(update_fields=["state", "data", "updated_at"])
        session.mark_closed(VoiceSession.Status.ABANDONED)
        return "Okay, I cancelled this booking request. No appointment was saved.", None
    if not parsing.is_yes(text):
        return "Please say yes to confirm, or no to cancel.", "yes_no"

    starts_at = session.data.get("starts_at")
    doctor_id = session.data.get("doctor_id")
    if not starts_at or not doctor_id:
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            "I am missing the appointment time, so I did not book anything. "
            "Please say a day and time again.",
            "datetime",
        )

    # Re-check before create: slot may have been taken during the confirmation pause.
    recheck = run_tool(
        "check_availability",
        {
            "doctor_id": doctor_id,
            "date": starts_at[:10],
            "preferred_starts_at": starts_at,
        },
    )
    if not recheck.get("ok"):
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return honest_backend_failure("availability check failed") + " Please choose another time.", "datetime"
    if not (recheck.get("result") or {}).get("preferred_available"):
        return _recover_from_taken_slot(
            session,
            alternatives=(recheck.get("result") or {}).get("alternatives")
            or (recheck.get("result") or {}).get("slots")
            or [],
        )

    idem_key = session.data.get("idempotency_key") or "voice-{}".format(session.id)
    session.data["idempotency_key"] = idem_key
    booked = run_tool(
        "create_appointment",
        {
            "doctor_id": doctor_id,
            "starts_at": starts_at,
            "patient_name": session.data["patient_name"],
            "contact_number": session.data["contact_number"],
            "reason": session.data["reason"],
            "idempotency_key": idem_key,
        },
    )
    if not booked.get("ok"):
        error = booked.get("error") or {}
        if error.get("code") == "slot_unavailable":
            return _recover_from_taken_slot(session, booked.get("alternatives") or [])
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            "I could not complete the booking and no appointment was saved. {}".format(
                error.get("message", "Please choose another time.")
            ),
            "datetime",
        )

    appointment = (booked.get("result") or {}).get("appointment") or {}
    reference = appointment.get("booking_reference")
    if not reference:
        session.state = "ASK_DATETIME"
        session.data = dict(session.data)
        return (
            honest_backend_failure("missing booking reference")
            + " Please choose another time.",
            "datetime",
        )

    session.booking_reference = reference
    session.data["booking_reference"] = reference
    session.state = "COMPLETED"
    session.data = dict(session.data)
    session.save(update_fields=["booking_reference", "state", "data", "updated_at"])
    session.mark_closed(VoiceSession.Status.COMPLETED)
    return (
        "Your appointment is booked. Your booking reference is {}. Goodbye.".format(reference),
        None,
    )


def _recover_from_taken_slot(session, alternatives):
    if alternatives:
        session.data["offered_slots"] = alternatives[:5]
        session.state = "OFFER_ALTERNATIVES"
        session.data = dict(session.data)
        return (
            slots_list_message(
                session.data["offered_slots"],
                intro=(
                    "That time was taken during our conversation. "
                    "No appointment was booked yet. Here are other open times:"
                ),
            ),
            "slot_choice",
        )
    session.state = "ASK_DATETIME"
    session.data = dict(session.data)
    return (
        "That slot was taken during our conversation and no appointment was booked. "
        "Please choose another day and time.",
        "datetime",
    )
