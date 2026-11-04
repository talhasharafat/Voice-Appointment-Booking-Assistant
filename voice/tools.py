"""
Structured backend tools for the voice booking assistant.

These tools call Django booking/clinic services. The browser only sends text
from free Web Speech recognition; it never books a slot by itself.
"""

import re

from booking import services
from booking.services import BookingError
from clinic.models import Specialty


TOOL_SPECS = (
    {
        "name": "match_specialty",
        "description": (
            "Match a patient's reason for visit to registered specialties. "
            "Ask a clarifying question when the match is ambiguous."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Brief reason for the visit."},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "find_doctors",
        "description": "List active doctors for a specialty id.",
        "parameters": {
            "type": "object",
            "properties": {
                "specialty_id": {"type": "integer"},
            },
            "required": ["specialty_id"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Check real free appointment slots for a doctor on a date. "
            "If preferred_starts_at is unavailable, alternatives are included."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doctor_id": {"type": "integer"},
                "date": {"type": "string", "description": "YYYY-MM-DD in clinic timezone."},
                "preferred_starts_at": {
                    "type": "string",
                    "description": "Optional ISO-8601 datetime.",
                },
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
            "required": ["doctor_id"],
        },
    },
    {
        "name": "create_appointment",
        "description": (
            "Create a confirmed appointment only after the patient explicitly confirms. "
            "Backend re-checks availability. Never invent a booking reference."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doctor_id": {"type": "integer"},
                "starts_at": {"type": "string"},
                "patient_name": {"type": "string"},
                "contact_number": {"type": "string"},
                "reason": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": [
                "doctor_id",
                "starts_at",
                "patient_name",
                "contact_number",
                "reason",
                "idempotency_key",
            ],
        },
    },
)


_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "have",
    "i",
    "my",
    "of",
    "on",
    "the",
    "to",
    "with",
    "need",
    "want",
    "please",
    "appointment",
    "doctor",
    "visit",
    "see",
}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [word for word in words if word not in _STOPWORDS and len(word) > 2]


def match_specialty(reason):
    """
    Score specialties from registered names/descriptions.

    Returns a single match, an ambiguous list with a clarifying question,
    or no_match guidance. Does not diagnose.
    """
    reason = (reason or "").strip()
    if not reason:
        return {
            "status": "needs_reason",
            "message": "Please share a brief reason for the visit.",
            "matches": [],
        }

    reason_tokens = set(_tokens(reason))
    reason_lower = reason.lower()
    scored = []
    for specialty in Specialty.objects.all():
        corpus = "{} {}".format(specialty.name, specialty.description).lower()
        corpus_tokens = set(_tokens(corpus))
        overlap = reason_tokens & corpus_tokens
        score = len(overlap)
        # Prefer exact specialty-name mention.
        if specialty.name.lower() in reason_lower:
            score += 5
        if score:
            scored.append(
                {
                    "specialty_id": specialty.id,
                    "name": specialty.name,
                    "description": specialty.description,
                    "score": score,
                    "matched_terms": sorted(overlap),
                }
            )

    scored.sort(key=lambda item: (-item["score"], item["name"]))
    if not scored:
        names = [item.name for item in Specialty.objects.order_by("name")]
        return {
            "status": "no_match",
            "message": (
                "I could not match that to a clinic specialty. "
                "We currently offer: {}.".format(", ".join(names) or "none configured")
            ),
            "matches": [],
            "clarifying_question": (
                "Is this more of a general illness, or a skin, hair, or nail concern?"
            ),
        }

    best = scored[0]
    contenders = [item for item in scored if item["score"] >= best["score"] - 1]
    if len(contenders) > 1 and best["score"] < 5:
        options = ", or ".join(item["name"] for item in contenders[:3])
        return {
            "status": "ambiguous",
            "message": "That could fit more than one specialty.",
            "matches": contenders[:3],
            "clarifying_question": "Would you like {}?".format(options),
        }

    return {
        "status": "matched",
        "message": "Matched specialty {}.".format(best["name"]),
        "matches": [best],
        "specialty_id": best["specialty_id"],
        "specialty_name": best["name"],
    }


def find_doctors(specialty_id):
    doctors = services.find_doctors(specialty_id=specialty_id)
    return {
        "status": "ok" if doctors else "none",
        "specialty_id": specialty_id,
        "doctors": doctors,
        "message": (
            "Found {} doctor(s).".format(len(doctors))
            if doctors
            else "No active doctors for that specialty."
        ),
    }


def check_availability(doctor_id, date=None, preferred_starts_at=None, date_from=None, date_to=None):
    payload = services.check_availability(
        doctor_id=doctor_id,
        day=date,
        date_from=date_from,
        date_to=date_to,
        preferred_starts_at=preferred_starts_at,
    )
    preferred_available = payload.get("preferred_available")
    if preferred_starts_at and preferred_available is False:
        status = "preferred_unavailable"
        message = "That time is not free. Here are alternatives."
    elif payload["slots"]:
        status = "available"
        message = "Found {} open slot(s).".format(len(payload["slots"]))
    else:
        status = "none"
        message = "No open slots in that window."
    payload["status"] = status
    payload["message"] = message
    return payload


def create_appointment(
    doctor_id,
    starts_at,
    patient_name,
    contact_number,
    reason,
    idempotency_key,
):
    """Backend-validated booking. Success only if the service confirms."""
    appointment = services.create_appointment(
        doctor_id=doctor_id,
        starts_at=starts_at,
        patient_name=patient_name,
        contact_number=contact_number,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return {
        "status": "booked" if appointment.get("created") else "replayed",
        "message": "Appointment confirmed." if appointment.get("created") else "Existing booking returned.",
        "appointment": appointment,
    }


TOOL_HANDLERS = {
    "match_specialty": lambda **kwargs: match_specialty(kwargs.get("reason", "")),
    "find_doctors": lambda **kwargs: find_doctors(kwargs["specialty_id"]),
    "check_availability": lambda **kwargs: check_availability(
        doctor_id=kwargs["doctor_id"],
        date=kwargs.get("date"),
        preferred_starts_at=kwargs.get("preferred_starts_at"),
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    ),
    "create_appointment": lambda **kwargs: create_appointment(
        doctor_id=kwargs["doctor_id"],
        starts_at=kwargs["starts_at"],
        patient_name=kwargs["patient_name"],
        contact_number=kwargs["contact_number"],
        reason=kwargs["reason"],
        idempotency_key=kwargs["idempotency_key"],
    ),
}


def list_tools():
    return list(TOOL_SPECS)


def run_tool(name, arguments=None):
    """
    Execute one tool by name.

    Returns a stable envelope. Booking failures become structured errors; they
    never look like a successful booking.
    """
    arguments = arguments or {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "ok": False,
            "tool": name,
            "error": {"code": "unknown_tool", "message": "Unknown tool {!r}.".format(name)},
        }
    try:
        result = handler(**arguments)
        return {"ok": True, "tool": name, "result": result}
    except BookingError as exc:
        payload = {
            "ok": False,
            "tool": name,
            "error": {"code": exc.code, "message": exc.message},
        }
        if exc.code == "slot_unavailable":
            try:
                payload["alternatives"] = services.suggest_alternatives(
                    doctor_id=arguments.get("doctor_id"),
                    preferred_starts_at=arguments.get("starts_at"),
                    days_ahead=3,
                )
            except BookingError:
                payload["alternatives"] = []
        return payload
    except (TypeError, KeyError, ValueError) as exc:
        return {
            "ok": False,
            "tool": name,
            "error": {
                "code": "invalid_arguments",
                "message": "Invalid tool arguments: {}".format(exc),
            },
        }


def next_day_suggestions(doctor_id, starts_at_iso, days_ahead=3):
    """Helper used when a preferred slot disappears mid-conversation."""
    return services.suggest_alternatives(
        doctor_id=doctor_id,
        preferred_starts_at=starts_at_iso,
        days_ahead=days_ahead,
    )
