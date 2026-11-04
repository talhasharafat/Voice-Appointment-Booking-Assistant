from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from booking.models import Appointment
from clinic.models import Doctor, Patient, Specialty, Unavailability, Weekday, WorkingHours


WEEKDAY_RANGE = (
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
)


class Command(BaseCommand):
    help = (
        "Load synthetic specialties, doctors, hours, unavailability, "
        "patients, and occupied appointment slots."
    )

    def handle(self, *args, **options):
        tz = ZoneInfo(settings.CLINIC_TIMEZONE)
        duration = timedelta(minutes=settings.APPOINTMENT_DURATION_MINUTES)

        gp, _ = Specialty.objects.update_or_create(
            name="General Practice",
            defaults={
                "description": (
                    "First-contact care for cough, fever, sore throat, blood pressure, "
                    "fatigue, infections, routine check-ups, and other general illnesses. "
                    "Not for dedicated skin, rash, or dermatology concerns."
                )
            },
        )
        derm, _ = Specialty.objects.update_or_create(
            name="Dermatology",
            defaults={
                "description": (
                    "Skin, hair, and nail problems including rash, acne, eczema, "
                    "psoriasis, moles, itching, and other dermatology concerns."
                )
            },
        )

        amina, _ = Doctor.objects.update_or_create(
            name="Dr. Amina Khan",
            defaults={"specialty": gp, "is_active": True},
        )
        bilal, _ = Doctor.objects.update_or_create(
            name="Dr. Bilal Hussain",
            defaults={"specialty": gp, "is_active": True},
        )
        sara, _ = Doctor.objects.update_or_create(
            name="Dr. Sara Malik",
            defaults={"specialty": derm, "is_active": True},
        )

        self._set_weekday_hours(amina, WEEKDAY_RANGE, time(9, 0), time(17, 0))
        self._set_weekday_hours(bilal, WEEKDAY_RANGE, time(10, 0), time(18, 0))
        self._set_weekday_hours(
            sara,
            (Weekday.TUESDAY, Weekday.THURSDAY, Weekday.SATURDAY),
            time(9, 0),
            time(15, 0),
        )

        Unavailability.objects.update_or_create(
            doctor=amina,
            starts_at=self._slot(tz, Weekday.FRIDAY, 12, 0),
            defaults={
                "ends_at": self._slot(tz, Weekday.FRIDAY, 14, 0),
                "reason": "Clinic meeting",
            },
        )

        patients = {
            "ahmed": self._patient("Ahmed Raza", "03001234567"),
            "fatima": self._patient("Fatima Noor", "03007654321"),
            "omar": self._patient("Omar Sheikh", "03219876543"),
        }

        occupied = (
            ("SEED-A1", "seed-amina-mon-0900", amina, patients["ahmed"], Weekday.MONDAY, 9, 0, "Persistent cough"),
            ("SEED-A2", "seed-amina-mon-0930", amina, patients["fatima"], Weekday.MONDAY, 9, 30, "Blood pressure review"),
            ("SEED-B1", "seed-bilal-mon-1000", bilal, patients["omar"], Weekday.MONDAY, 10, 0, "Fever and fatigue"),
            ("SEED-S1", "seed-sara-tue-0900", sara, patients["ahmed"], Weekday.TUESDAY, 9, 0, "Facial acne follow-up"),
        )
        for reference, key, doctor, patient, weekday, hour, minute, reason in occupied:
            starts_at = self._slot(tz, weekday, hour, minute)
            Appointment.objects.update_or_create(
                booking_reference=reference,
                defaults={
                    "patient": patient,
                    "doctor": doctor,
                    "starts_at": starts_at,
                    "ends_at": starts_at + duration,
                    "reason": reason,
                    "status": Appointment.Status.CONFIRMED,
                    "idempotency_key": key,
                },
            )

        self.stdout.write(self.style.SUCCESS("Sample clinic data is ready."))
        self.stdout.write(
            "Occupied slots: Amina Mon 09:00 and 09:30, Bilal Mon 10:00, Sara Tue 09:00 "
            "({}). Other in-hours slots stay free.".format(settings.CLINIC_TIMEZONE)
        )

    def _set_weekday_hours(self, doctor, weekdays, start_time, end_time):
        for weekday in weekdays:
            WorkingHours.objects.update_or_create(
                doctor=doctor,
                weekday=weekday,
                start_time=start_time,
                defaults={"end_time": end_time},
            )

    def _patient(self, name, contact_number):
        patient, _ = Patient.objects.update_or_create(
            name=name,
            contact_number=contact_number,
            defaults={},
        )
        return patient

    def _slot(self, tz, weekday, hour, minute):
        now = timezone.now().astimezone(tz)
        days_ahead = (weekday - now.weekday()) % 7
        candidate = datetime.combine(now.date() + timedelta(days=days_ahead), time(hour, minute), tzinfo=tz)
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        return candidate
