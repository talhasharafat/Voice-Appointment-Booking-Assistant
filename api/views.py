"""
HTTP API under /api/.

Domain logic lives in booking.services — this module only adapts HTTP.
"""

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from booking import services
from booking.services import BookingError, SlotUnavailable


def _status_for(exc):
    return {
        "doctor_not_found": status.HTTP_404_NOT_FOUND,
        "specialty_not_found": status.HTTP_404_NOT_FOUND,
        "slot_unavailable": status.HTTP_409_CONFLICT,
        "invalid_request": status.HTTP_400_BAD_REQUEST,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
def specialties(request):
    return Response(services.list_specialties())


@api_view(["GET"])
def doctors(request):
    specialty_id = request.query_params.get("specialty_id")
    try:
        parsed = int(specialty_id) if specialty_id not in (None, "") else None
        rows = services.find_doctors(specialty_id=parsed)
    except (TypeError, ValueError):
        return Response(
            {"detail": "specialty_id must be an integer.", "code": "invalid_request"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except BookingError as exc:
        return Response(
            {"detail": exc.message, "code": exc.code},
            status=_status_for(exc),
        )

    return Response(
        [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row.get("specialty_description", ""),
                "specialty": {
                    "id": row["specialty_id"],
                    "name": row["specialty_name"],
                    "description": row.get("specialty_description", ""),
                },
                "timezone": row.get("timezone", "Asia/Karachi"),
            }
            for row in rows
        ]
    )


@api_view(["GET"])
def availability(request):
    try:
        doctor_id = int(request.query_params.get("doctor_id", ""))
    except (TypeError, ValueError):
        return Response(
            {"detail": "doctor_id must be an integer.", "code": "invalid_request"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    day = request.query_params.get("date")
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    time_from = request.query_params.get("time_from")
    time_to = request.query_params.get("time_to")
    preferred = request.query_params.get("preferred_starts_at")

    try:
        result = services.check_availability(
            doctor_id=doctor_id,
            day=day if day and not date_from else None,
            date_from=date_from,
            date_to=date_to,
            preferred_starts_at=preferred,
        )
    except BookingError as exc:
        return Response(
            {"detail": exc.message, "code": exc.code},
            status=_status_for(exc),
        )

    slots = result["slots"]
    if time_from or time_to:
        filtered = []
        for slot in slots:
            stamp = slot["starts_at"][11:16]
            if time_from and stamp < time_from:
                continue
            if time_to and stamp > time_to:
                continue
            filtered.append(slot)
        slots = filtered

    return Response(
        {
            "clinic_timezone": result["clinic_timezone"],
            "duration_minutes": result["duration_minutes"],
            "doctor": result["doctor"],
            "date_from": result["date_from"],
            "date_to": result["date_to"],
            "slots": slots,
            "preferred_starts_at": result.get("preferred_starts_at"),
            "preferred_available": result.get("preferred_available"),
            "alternatives": result.get("alternatives") or [],
        }
    )


@api_view(["POST"])
def create_appointment(request):
    body = request.data if isinstance(request.data, dict) else {}
    patient = body.get("patient") if isinstance(body.get("patient"), dict) else {}
    doctor_id = body.get("doctor_id")
    starts_at = body.get("starts_at")
    try:
        payload = services.create_appointment(
            doctor_id=doctor_id,
            starts_at=starts_at,
            patient_name=body.get("patient_name") or patient.get("name"),
            contact_number=(
                body.get("contact_number")
                or body.get("phone")
                or patient.get("phone")
                or patient.get("contact_number")
            ),
            email=body.get("email") or patient.get("email") or "",
            reason=body.get("reason") or body.get("reason_for_visit"),
            idempotency_key=body.get("idempotency_key"),
        )
    except SlotUnavailable as exc:
        alternatives = []
        if doctor_id and starts_at:
            try:
                alternatives = services.suggest_alternatives(
                    doctor_id=doctor_id,
                    preferred_starts_at=starts_at,
                )
            except BookingError:
                alternatives = []
        return Response(
            {
                "detail": exc.message,
                "code": exc.code,
                "alternatives": alternatives,
            },
            status=status.HTTP_409_CONFLICT,
        )
    except BookingError as exc:
        return Response(
            {"detail": exc.message, "code": exc.code},
            status=_status_for(exc),
        )

    status_code = status.HTTP_201_CREATED if payload.get("created") else status.HTTP_200_OK
    return Response(
        {
            "booking_reference": payload["booking_reference"],
            "status": payload["status"],
            "starts_at": payload["starts_at"],
            "ends_at": payload["ends_at"],
            "doctor": payload["doctor"],
            "patient": payload["patient"],
            "created": payload.get("created"),
            "replayed": payload.get("replayed", False),
        },
        status=status_code,
    )
