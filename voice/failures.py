"""
Honest failure helpers for the voice booking flow.

Rules:
- Never claim a booking succeeded unless booking.services returned a reference.
- Prefer clarification over guessing unclear speech or incomplete dates.
- Abandoned / failed sessions must not leave unconfirmed appointments.
"""

from django.conf import settings
from django.utils import timezone

from voice.models import VoiceSession


class ConversationFault(Exception):
    """Unexpected engine failure; surfaced as an honest assistant reply."""

    def __init__(self, message, *, code="engine_error", retryable=True):
        self.message = message
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def honest_backend_failure(detail=None):
    base = (
        "I hit a clinic system problem and did not book an appointment. "
        "Please try again in a moment."
    )
    if detail:
        return "{} ({})".format(base, detail)
    return base


def honest_provider_failure():
    return (
        "Speech processing had a problem on this device. "
        "No appointment was booked. You can type your reply instead."
    )


def session_timed_out(session):
    minutes = int(getattr(settings, "VOICE_SESSION_TIMEOUT_MINUTES", 30))
    if session.status != VoiceSession.Status.ACTIVE:
        return False
    idle = timezone.now() - session.updated_at
    return idle.total_seconds() > minutes * 60


def expire_if_stale(session):
    """Close idle sessions without creating appointments."""
    if not session_timed_out(session):
        return False
    session.state = "ABANDONED"
    session.last_assistant_message = (
        "This booking chat timed out and was closed. No appointment was booked."
    )
    session.save(update_fields=["state", "last_assistant_message", "updated_at"])
    session.mark_closed(VoiceSession.Status.ABANDONED)
    return True
