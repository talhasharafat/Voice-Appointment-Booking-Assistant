import uuid

from django.db import models
from django.utils import timezone


class VoiceSession(models.Model):
    """
    In-progress browser conversation.

    Draft patient details live here only. Appointments are created after explicit
    confirmation through booking tools — abandoning a session creates no booking.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    state = models.CharField(max_length=40, default="GREETING")
    data = models.JSONField(default=dict, blank=True)
    last_assistant_message = models.TextField(blank=True)
    booking_reference = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def mark_closed(self, status):
        self.status = status
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])

    def __str__(self):
        return "{} ({})".format(self.id, self.state)
