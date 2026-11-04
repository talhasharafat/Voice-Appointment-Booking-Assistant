import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from voice import conversation
from voice.failures import honest_backend_failure

logger = logging.getLogger(__name__)


def _body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _safe_json(handler, request):
    try:
        return handler(request)
    except Exception:
        logger.exception("voice API failure on %s", request.path)
        return JsonResponse(
            {
                "ok": False,
                "error": {
                    "code": "server_error",
                    "message": honest_backend_failure(),
                },
                "booked": False,
            },
            status=500,
        )


@require_http_methods(["POST"])
@csrf_exempt
def start(request):
    return _safe_json(lambda _req: JsonResponse(conversation.start_session()), request)


@require_http_methods(["POST"])
@csrf_exempt
def turn(request):
    def _inner(_req):
        body = _body(request)
        if body is None:
            return JsonResponse(
                {
                    "ok": False,
                    "booked": False,
                    "error": {"code": "invalid_json", "message": "Body must be JSON."},
                },
                status=400,
            )
        session_id = body.get("session_id")
        text = body.get("text", "")
        if not session_id:
            return JsonResponse(
                {
                    "ok": False,
                    "booked": False,
                    "error": {"code": "missing_session", "message": "session_id is required."},
                },
                status=400,
            )
        payload = conversation.process_turn(session_id, text)
        payload["booked"] = bool((payload.get("data") or {}).get("booking_reference")) and payload.get(
            "state"
        ) == "COMPLETED"
        status = 404 if payload.get("error", {}).get("code") == "not_found" else 200
        return JsonResponse(payload, status=status)

    return _safe_json(_inner, request)


@require_http_methods(["POST"])
@csrf_exempt
def abandon(request):
    def _inner(_req):
        body = _body(request)
        if body is None or not body.get("session_id"):
            return JsonResponse(
                {
                    "ok": False,
                    "booked": False,
                    "error": {"code": "missing_session", "message": "session_id is required."},
                },
                status=400,
            )
        payload = conversation.abandon_session(body["session_id"])
        payload["booked"] = False
        status = 404 if payload.get("error", {}).get("code") == "not_found" else 200
        return JsonResponse(payload, status=status)

    return _safe_json(_inner, request)


@require_GET
def health(request):
    return JsonResponse(
        {
            "ok": True,
            "speech_backend": "browser",
            "ai_provider": "local",
            "message": "Free voice booking conversation API",
        }
    )


@require_GET
def assistant(request):
    return render(
        request,
        "voice/assistant.html",
        {
            "clinic_timezone": settings.CLINIC_TIMEZONE,
            "speech_backend": getattr(settings, "SPEECH_BACKEND", "browser"),
        },
    )
