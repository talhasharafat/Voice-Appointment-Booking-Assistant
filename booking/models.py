from django.core.exceptions import ValidationError
from django.db import models


class Appointment(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(
        "clinic.Patient",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    doctor = models.ForeignKey(
        "clinic.Doctor",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CONFIRMED,
    )
    booking_reference = models.CharField(max_length=20, unique=True)
    idempotency_key = models.CharField(
        max_length=64,
        unique=True,
        help_text="Client-supplied key so a retried booking request cannot create a second appointment.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(ends_at__gt=models.F("starts_at")),
                name="appointment_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["doctor", "starts_at"],
                condition=models.Q(status="confirmed"),
                name="uniq_confirmed_doctor_slot",
            ),
        ]

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError("Appointment must end after it starts.")

    def __str__(self):
        return "{} {} with {}".format(
            self.booking_reference,
            self.starts_at,
            self.doctor,
        )
