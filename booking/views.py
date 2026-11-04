import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from booking import services
from booking.services import BookingError


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise services.InvalidBookingRequest("Request body must be valid JSON.") from exc


def _error_response(exc, status=400):
    return JsonResponse({"ok": False, "error": {"code": exc.code, "message": exc.message}}, status=status)


@require_GET
def specialties(request):
    return JsonResponse({"ok": True, "specialties": services.list_specialties()})


@require_GET
def doctors(request):
    specialty_id = request.GET.get("specialty_id")
    try:
        parsed_id = int(specialty_id) if specialty_id not in (None, "") else None
        payload = services.find_doctors(specialty_id=parsed_id)
    except (TypeError, ValueError):
        return _error_response(services.InvalidBookingRequest("specialty_id must be an integer."))
    except BookingError as exc:
        status = 404 if exc.code == "specialty_not_found" else 400
        return _error_response(exc, status=status)
    return JsonResponse({"ok": True, "doctors": payload})


@require_GET
def availability(request):
    try:
        doctor_id = int(request.GET.get("doctor_id", ""))
        payload = services.check_availability(
            doctor_id=doctor_id,
            day=request.GET.get("date"),
            date_from=request.GET.get("date_from"),
            date_to=request.GET.get("date_to"),
            preferred_starts_at=request.GET.get("preferred_starts_at"),
        )
    except (TypeError, ValueError):
        return _error_response(services.InvalidBookingRequest("doctor_id must be an integer."))
    except BookingError as exc:
        status = 404 if exc.code == "doctor_not_found" else 400
        return _error_response(exc, status=status)
    return JsonResponse({"ok": True, "availability": payload})


@csrf_exempt
@require_http_methods(["POST"])
def create_appointment(request):
    body = {}
    try:
        body = _json_body(request)
        payload = services.create_appointment(
            doctor_id=body.get("doctor_id"),
            starts_at=body.get("starts_at"),
            patient_name=body.get("patient_name"),
            contact_number=body.get("contact_number"),
            reason=body.get("reason"),
            idempotency_key=body.get("idempotency_key"),
        )
    except BookingError as exc:
        status = {
            "doctor_not_found": 404,
            "slot_unavailable": 409,
            "invalid_request": 400,
        }.get(exc.code, 400)
        response = _error_response(exc, status=status)
        if exc.code == "slot_unavailable" and body.get("doctor_id") and body.get("starts_at"):
            try:
                alternatives = services.suggest_alternatives(
                    doctor_id=body.get("doctor_id"),
                    preferred_starts_at=body.get("starts_at"),
                )
                data = json.loads(response.content.decode("utf-8"))
                data["alternatives"] = alternatives
                return JsonResponse(data, status=status)
            except BookingError:
                return response
        return response
    return JsonResponse({"ok": True, "appointment": payload}, status=201 if payload.get("created") else 200)
