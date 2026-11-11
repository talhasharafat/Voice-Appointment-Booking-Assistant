"""
HTTP tools for the LiveKit booking agent.

Talks to Django only over HTTP (`DJANGO_API_BASE`).
Django remains the sole source of truth for availability and booking.
Nothing is persisted until create_appointment succeeds.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DJANGO_API_BASE = os.getenv("DJANGO_API_BASE", "http://127.0.0.1:8000/api/").rstrip("/") + "/"
HTTP_TIMEOUT = float(os.getenv("DJANGO_API_TIMEOUT", "15"))

_STOPWORDS = {
    "a", "an", "and", "for", "have", "i", "my", "of", "on", "the", "to", "with",
    "need", "want", "please", "appointment", "doctor", "visit", "see",
}


def _client() -> httpx.Client:
    return httpx.Client(base_url=DJANGO_API_BASE, timeout=HTTP_TIMEOUT)


def _error_payload(exc: Exception, *, code: str = "http_error") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": str(exc)}}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [word for word in words if word not in _STOPWORDS and len(word) > 2]


def _fetch_specialties() -> list[dict[str, Any]]:
    with _client() as client:
        response = client.get("specialties/")
        response.raise_for_status()
        body = response.json()
    if isinstance(body, list):
        return body
    return body.get("specialties") or []


def _next_open_slots(doctor_id: int, days: int = 7, limit: int = 3) -> list[dict[str, Any]]:
    today = date.today()
    slots: list[dict[str, Any]] = []
    with _client() as client:
        for offset in range(days):
            day = today + timedelta(days=offset)
            response = client.get(
                "availability/",
                params={"doctor_id": doctor_id, "date": day.isoformat()},
            )
            if response.status_code >= 400:
                continue
            body = response.json()
            for slot in body.get("slots") or []:
                slots.append(slot)
                if len(slots) >= limit:
                    return slots
    return slots


def match_specialty(reason: str) -> dict[str, Any]:
    """
    Step 2 of the patient journey: map reason → specialty via Django specialty list.

    Returns matched | ambiguous | no_match. Does not diagnose.
    """
    try:
        reason = (reason or "").strip()
        if not reason:
            return {
                "ok": True,
                "status": "needs_reason",
                "message": "Please share a brief reason for the visit.",
                "matches": [],
            }

        specialties = _fetch_specialties()
        reason_tokens = set(_tokens(reason))
        reason_lower = reason.lower()
        scored = []
        for specialty in specialties:
            corpus = "{} {}".format(
                specialty.get("name", ""),
                specialty.get("description", ""),
            ).lower()
            overlap = reason_tokens & set(_tokens(corpus))
            score = len(overlap)
            if specialty.get("name", "").lower() in reason_lower:
                score += 5
            if score:
                scored.append(
                    {
                        "specialty_id": specialty["id"],
                        "name": specialty.get("name"),
                        "description": specialty.get("description"),
                        "score": score,
                        "matched_terms": sorted(overlap),
                    }
                )

        scored.sort(key=lambda item: (-item["score"], item["name"] or ""))
        if not scored:
            names = [item.get("name") for item in specialties]
            return {
                "ok": True,
                "status": "no_match",
                "message": (
                    "Could not match that reason to a specialty. "
                    "Clinic offers: {}.".format(", ".join(n for n in names if n) or "none")
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
                "ok": True,
                "status": "ambiguous",
                "message": "That could fit more than one specialty.",
                "matches": contenders[:3],
                "clarifying_question": (
                    "Ask exactly one clarifying question. "
                    "Would you like {}?".format(options)
                ),
            }

        return {
            "ok": True,
            "status": "matched",
            "message": "Matched specialty {}.".format(best["name"]),
            "matches": [best],
            "specialty_id": best["specialty_id"],
            "specialty_name": best["name"],
            "specialty_description": best.get("description"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("match_specialty failed")
        return _error_payload(exc)


def find_doctors(specialty_id: Optional[int] = None, specialty_query: str = "") -> dict[str, Any]:
    """
    Step 3: list active doctors for a specialty (id preferred).

    Includes a few next open slots per doctor from Django availability.
    """
    try:
        if specialty_id is None and specialty_query:
            matched = match_specialty(specialty_query)
            if matched.get("status") == "matched":
                specialty_id = matched.get("specialty_id")
            elif matched.get("status") == "ambiguous":
                return matched
            else:
                return matched

        if specialty_id is None:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": "specialty_id is required (run match_specialty first).",
                },
            }

        with _client() as client:
            response = client.get("doctors/", params={"specialty_id": int(specialty_id)})
            response.raise_for_status()
            doctors = response.json()
            if not isinstance(doctors, list):
                doctors = doctors.get("doctors") or []

        enriched = []
        for doctor in doctors:
            doctor_id = doctor["id"]
            enriched.append(
                {
                    "id": doctor_id,
                    "name": doctor.get("name"),
                    "specialty": doctor.get("specialty"),
                    "timezone": doctor.get("timezone", "Asia/Karachi"),
                    "next_open_slots": _next_open_slots(doctor_id),
                }
            )
        return {
            "ok": True,
            "status": "ok" if enriched else "none",
            "specialty_id": int(specialty_id),
            "doctors": enriched,
            "message": (
                "Found {} doctor(s).".format(len(enriched))
                if enriched
                else "No active doctors for that specialty."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("find_doctors failed")
        return _error_payload(exc)


def check_availability(
    doctor_id: int,
    date: str,
    preferred_starts_at: Optional[str] = None,
) -> dict[str, Any]:
    """
    Step 3–4: return open slots for a doctor on a date (YYYY-MM-DD).

    If preferred_starts_at is set and taken, includes alternatives.
    """
    try:
        params = {"doctor_id": int(doctor_id), "date": date}
        if preferred_starts_at:
            params["preferred_starts_at"] = preferred_starts_at
        with _client() as client:
            response = client.get("availability/", params=params)
            if response.status_code == 404:
                return {
                    "ok": False,
                    "error": {
                        "code": "doctor_not_found",
                        "message": response.json().get("detail", "Doctor not found."),
                    },
                }
            response.raise_for_status()
            body = response.json()

        preferred_available = body.get("preferred_available")
        slots = body.get("slots") or []
        alternatives = body.get("alternatives") or []
        if preferred_starts_at and preferred_available is False:
            status = "preferred_unavailable"
            message = "Preferred time is not free. Offer alternatives from the list."
            if not alternatives:
                alternatives = slots[:5]
        elif slots:
            status = "available"
            message = "Found {} open slot(s).".format(len(slots))
        else:
            status = "none"
            message = "No open slots that day. Ask for another day."

        return {
            "ok": True,
            "status": status,
            "message": message,
            "doctor": body.get("doctor"),
            "date": body.get("date_from") or date,
            "slots": slots,
            "preferred_starts_at": preferred_starts_at,
            "preferred_available": preferred_available,
            "alternatives": alternatives,
            "duration_minutes": body.get("duration_minutes"),
            "clinic_timezone": body.get("clinic_timezone", "Asia/Karachi"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("check_availability failed")
        return _error_payload(exc)


def create_appointment(
    patient_name: str,
    phone: str,
    doctor_id: int,
    slot_start: str,
    reason: str,
    idempotency_key: str,
    email: Optional[str] = "",
) -> dict[str, Any]:
    """
    Step 6: create booking only after explicit patient yes.

    Abandoned chats must never call this. Success only when Django returns a reference.
    Re-checks availability first so the LLM cannot invent a free slot.
    """
    try:
        day = (slot_start or "")[:10]
        if day:
            availability = check_availability(
                doctor_id=doctor_id,
                date=day,
                preferred_starts_at=slot_start,
            )
            if availability.get("ok") and availability.get("preferred_available") is False:
                return {
                    "ok": False,
                    "status": "preferred_unavailable",
                    "error": {
                        "code": "slot_unavailable",
                        "message": (
                            "That time is not free. Do not claim a booking. "
                            "Offer alternatives from this result."
                        ),
                    },
                    "alternatives": availability.get("alternatives")
                    or availability.get("slots")
                    or [],
                    "message": "Preferred slot unavailable. Offer real alternatives only.",
                }

        payload = {
            "doctor_id": int(doctor_id),
            "starts_at": slot_start,
            "patient_name": patient_name,
            "phone": phone,
            "contact_number": phone,
            "reason": reason,
            "idempotency_key": idempotency_key,
            "email": email or "",
        }
        with _client() as client:
            response = client.post("appointments/", json=payload)
            try:
                body = response.json()
            except Exception:  # noqa: BLE001
                body = {"detail": response.text}

            if response.status_code in (200, 201):
                return {
                    "ok": True,
                    "status": "booked",
                    "booking_reference": body.get("booking_reference"),
                    "appointment_status": body.get("status"),
                    "starts_at": body.get("starts_at"),
                    "ends_at": body.get("ends_at"),
                    "doctor": body.get("doctor"),
                    "patient": body.get("patient"),
                    "created": body.get("created"),
                    "replayed": body.get("replayed", False),
                    "message": "Booking confirmed by backend.",
                }

            if response.status_code == 409:
                return {
                    "ok": False,
                    "status": "conflict",
                    "error": {
                        "code": body.get("code") or "slot_unavailable",
                        "message": body.get("detail") or "That slot is not available.",
                    },
                    "alternatives": body.get("alternatives") or [],
                    "message": "Slot taken. Offer alternatives; do not claim success.",
                }

            return {
                "ok": False,
                "status": "error",
                "error": {
                    "code": body.get("code") or "booking_error",
                    "message": body.get("detail") or "Booking failed.",
                },
                "message": "Backend failed. Say so honestly. No booking was made.",
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_appointment failed")
        return _error_payload(exc)
