from django.core.exceptions import ValidationError
from django.db import models


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class Specialty(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(
        help_text="Used to match a patient's reason for visit to this specialty."
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "specialties"

    def __str__(self):
        return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=150)
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.PROTECT,
        related_name="doctors",
    )
    is_active = models.BooleanField(default=True)
    # Clinic-wide timezone; kept on the doctor for clarity in APIs/admin.
    timezone = models.CharField(max_length=64, default="Asia/Karachi")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return "{} ({})".format(self.name, self.specialty.name)


class WorkingHours(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name="working_hours",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["doctor", "weekday", "start_time"]
        verbose_name_plural = "working hours"
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="working_hours_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["doctor", "weekday", "start_time"],
                name="uniq_doctor_weekday_start",
            ),
        ]

    def clean(self):
        if self.end_time and self.start_time and self.end_time <= self.start_time:
            raise ValidationError("Working hours must end after they start.")

    def __str__(self):
        return "{} {} {}–{}".format(
            self.doctor,
            self.get_weekday_display(),
            self.start_time,
            self.end_time,
        )


class Unavailability(models.Model):
    """Blocked time for a doctor outside normal bookings."""


    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name="unavailabilities",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["starts_at"]
        verbose_name = "unavailable period"
        verbose_name_plural = "unavailable periods"
        constraints = [
            models.CheckConstraint(
                check=models.Q(ends_at__gt=models.F("starts_at")),
                name="unavailability_end_after_start",
            ),
        ]

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError("Unavailability must end after it starts.")

    def __str__(self):
        return "{} unavailable {}–{}".format(self.doctor, self.starts_at, self.ends_at)


class Patient(models.Model):
    """Minimum fields required to book: identity and a way to contact the patient."""

    name = models.CharField(max_length=150)
    contact_number = models.CharField(max_length=30, help_text="Phone number")
    email = models.EmailField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "contact_number"],
                name="uniq_patient_name_contact",
            ),
        ]

    @property
    def phone(self):
        return self.contact_number

    def __str__(self):
        return "{} ({})".format(self.name, self.contact_number)

